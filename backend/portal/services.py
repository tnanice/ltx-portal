import asyncio
import json
import secrets
import time
from collections import OrderedDict

from fastapi import HTTPException

from .provider import UpstreamError


class Services:
    def __init__(self, db, vault, provider, catalog):
        self.db, self.vault, self.provider, self.catalog = db, vault, provider, catalog
        self.qrs = {}
        self.jobs = set()
        self.active_tasks = set()
        self.cache = OrderedDict()
        self.locks = {}
        self.task_locks = {}
        self.direct_locks = [asyncio.Lock() for _ in range(32)]
        self.next_probe = 0
        # A stopped process may have sent the write. Reconcile, never blindly send again.
        db.run("UPDATE tasks SET state='unknown',next_check=0 WHERE state='submitting'")

    def spawn(self, coroutine):
        job = asyncio.create_task(coroutine)
        self.jobs.add(job)
        job.add_done_callback(self.jobs.discard)
        return job

    def lock(self, user_id):
        return self.locks.setdefault(user_id, asyncio.Lock())

    def task_lock(self, task_id):
        return self.task_locks.setdefault(task_id, asyncio.Lock())

    @staticmethod
    def check_delay(task):
        age = time.time() - task['created']
        return 2 if age < 60 else 5 if age < 300 else 10

    def binding(self, uid):
        row = self.db.one('SELECT b.* FROM bindings b JOIN users u ON u.id=b.user_id WHERE b.user_id=? AND u.disabled=0', (uid,))
        if not row or not row['cookie']:
            raise HTTPException(409, '请先绑定自己的115账号')
        return row

    def current(self, uid, revision):
        b = self.db.one('SELECT b.revision,b.cookie,u.disabled FROM bindings b JOIN users u ON u.id=b.user_id WHERE b.user_id=?', (uid,))
        return bool(b and b['cookie'] and b['revision'] == revision and not b['disabled'])

    def cookie(self, binding):
        return self.vault.decrypt(binding['cookie'])

    def revoke(self, uid):
        self.db.run('DELETE FROM tickets WHERE user_id=?', (uid,))
        for key in list(self.cache):
            if key[0] == uid:
                del self.cache[key]
        for qr in self.qrs.values():
            if qr['user_id'] == uid and qr['state'] not in ('success', 'expired', 'cancelled'):
                qr['state'] = 'cancelled'
                if qr.get('job'):
                    qr['job'].cancel()

    async def bind(self, uid, cookie, expected_revision=None, expected_session=None):
        async with self.lock(uid):
            account = await self.provider.verify(cookie)
            with self.db.connect() as c:
                old = c.execute('SELECT * FROM bindings WHERE user_id=?', (uid,)).fetchone()
                u = c.execute('SELECT disabled FROM users WHERE id=?', (uid,)).fetchone()
                rev = old['revision'] if old else 0
                if expected_session and not c.execute('SELECT token FROM sessions WHERE token=? AND user_id=? AND expires>?',
                                                      (expected_session, uid, time.time())).fetchone():
                    raise HTTPException(401, '登录已失效，请重新登录')
                if not u or u['disabled'] or (expected_revision is not None and rev != expected_revision):
                    raise HTTPException(409, '绑定状态已变化，请重新开始')
                c.execute('INSERT INTO bindings(user_id,revision,account_id,cookie,state,checked) VALUES(?,?,?,?,?,?) '
                          'ON CONFLICT(user_id) DO UPDATE SET revision=excluded.revision,account_id=excluded.account_id,'
                          'cookie=excluded.cookie,state=excluded.state,checked=excluded.checked,save_path=\'\'',
                          (uid, rev + 1, account, self.vault.encrypt(cookie), 'ok', time.time()))
                c.execute('DELETE FROM tickets WHERE user_id=?', (uid,))
            for key in list(self.cache):
                if key[0] == uid:
                    self.cache.pop(key, None)
            return {'state': 'ok', 'account_id': account, 'revision': rev + 1}

    async def check_binding(self, uid):
        b = self.binding(uid)
        try:
            await self.provider.verify(self.cookie(b))
            state = 'ok'
        except UpstreamError as e:
            state = 'invalid' if e.kind == 'invalid' else 'unreachable'
        self.db.run('UPDATE bindings SET state=?,checked=? WHERE user_id=? AND revision=?',
                    (state, time.time(), uid, b['revision']))
        return state

    async def qr_loop(self, qr_id):
        qr = self.qrs[qr_id]
        try:
            while time.time() < qr['expires'] and qr['state'] not in ('cancelled', 'expired', 'success'):
                try:
                    status = await self.provider.qr_poll(qr['token'])
                    qr['error'] = ''
                    if status in (-1, -2):
                        qr['state'] = 'expired' if status == -1 else 'cancelled'
                        return
                    if status == 2:
                        qr['state'] = 'verifying'
                        cookie = await self.provider.qr_result(qr['token'], qr['app'])
                        if time.time() >= qr['expires']:
                            qr['state'] = 'expired'
                            return
                        await self.bind(qr['user_id'], cookie, expected_revision=qr['revision'], expected_session=qr['session_token'])
                        qr['state'] = 'success'
                        return
                    qr['state'] = 'scanned' if status == 1 else 'waiting'
                except UpstreamError as e:
                    qr['error'] = e.message
                    qr['state'] = 'unreachable' if e.kind == 'temporary' else 'failed'
                    if qr['state'] == 'failed':
                        return
                await asyncio.sleep(2)
            if qr['state'] not in ('success', 'cancelled'):
                qr['state'] = 'expired'
        except asyncio.CancelledError:
            if qr['state'] != 'expired':
                qr['state'] = 'cancelled'
        except HTTPException:
            qr.update(state='cancelled', error='账号或绑定已变化，请重新扫码')
        finally:
            qr.pop('token', None)

    def task_update(self, task, **values):
        if not self.current(task['user_id'], task['revision']):
            return
        values['updated'] = time.time()
        self.db.run('UPDATE tasks SET ' + ','.join(f'{k}=?' for k in values) + ' WHERE id=?',
                    (*values.values(), task['id']))

    async def process_task(self, task_id):
        if task_id in self.active_tasks:
            return
        self.active_tasks.add(task_id)
        try:
            task = self.db.one('SELECT * FROM tasks WHERE id=?', (task_id,))
            if not task or not self.current(task['user_id'], task['revision']):
                return
            async with self.task_lock(task_id):
                task = self.db.one('SELECT * FROM tasks WHERE id=?', (task_id,))
                if not task or not self.current(task['user_id'], task['revision']):
                    return
                b = self.binding(task['user_id'])
                cookie = self.cookie(b)
                try:
                    if task['state'] == 'submitting':
                        try:
                            result = await self.provider.submit(cookie, task['link'], b['save_path'])
                            self.task_update(task, state='downloading', info_hash=result['info_hash'], error='', next_check=0)
                        except UpstreamError as e:
                            if e.kind == 'temporary':
                                self.task_update(task, state='unknown', error='提交结果待确认，正在核对115任务；不会重复提交', next_check=time.time()+2)
                            else:
                                self.task_update(task, state='failed', error=e.message)
                            return
                        task = self.db.one('SELECT * FROM tasks WHERE id=?', (task_id,))
                        if not task or not self.current(task['user_id'], task['revision']):
                            return
                        hint = result.get('task') or {}
                        if hint.get('pick_code') or hint.get('pickcode'):
                            files = await self.provider.files(cookie, hint)
                            self.task_update(task, state='completed', files=json.dumps(files, ensure_ascii=False), percent=100, error='')
                            return
                    if task['state'] not in ('unknown', 'downloading'):
                        return
                    page = task['scan_page']
                    for _ in range(3):
                        rows, total = await self.provider.task_page(cookie, page)
                        found = next((t for t in rows if str(t.get('info_hash', '')).lower() == task['info_hash']), None)
                        if found:
                            status = int(found.get('status', 0))
                            percent = min(100, max(0, float(found.get('percentDone') or 0)))
                            if status < 0:
                                self.task_update(task, state='failed', error='115离线任务失败，请在115中查看原因', percent=percent)
                            elif status == 2 or percent >= 100:
                                files = await self.provider.files(cookie, found)
                                self.task_update(task, state='completed', files=json.dumps(files, ensure_ascii=False), percent=100, error='')
                            else:
                                self.task_update(task, state='downloading', percent=percent, error='', scan_page=1, next_check=time.time()+self.check_delay(task))
                            return
                        if page >= total:
                            old = time.time() - task['created'] > 900
                            self.task_update(task, state='failed' if old else task['state'], scan_page=1,
                                             error='未在115中找到任务，请核对后手动重试' if old else '等待115任务可见', next_check=time.time()+self.check_delay(task))
                            return
                        page += 1
                    self.task_update(task, scan_page=page, next_check=time.time()+2)
                except UpstreamError as e:
                    if e.kind == 'invalid':
                        self.db.run("UPDATE bindings SET state='invalid',checked=? WHERE user_id=? AND revision=?",
                                    (time.time(), task['user_id'], task['revision']))
                    self.task_update(task, state='failed' if e.kind in ('invalid', 'rejected') else task['state'],
                                     error=e.message, next_check=time.time()+30)
        except asyncio.CancelledError:
            raise
        except Exception:
            if task:
                self.task_update(task, error='任务检查暂时失败，请稍后重试', next_check=time.time()+30)
        finally:
            self.active_tasks.discard(task_id)

    async def direct(self, ticket, ua):
        key = (ticket['user_id'], ticket['revision'], ticket['pickcode'], ua)
        now = time.time()
        cached = self.cache.get(key)
        if cached and cached[1] > now:
            return cached[0]
        async with self.direct_locks[hash(key) % len(self.direct_locks)]:
            b = self.binding(ticket['user_id'])
            if b['revision'] != ticket['revision']:
                raise HTTPException(410, '播放凭证已失效')
            cached = self.cache.get(key)
            if cached and cached[1] > time.time():
                return cached[0]
            target, expires = await self.provider.direct(self.cookie(b), ticket['pickcode'], ua)
            ttl = min(now + 60, expires - 30) if expires else now  # unknown expiry: do not cache
            self.cache[key] = (target, ttl)
            if len(self.cache) > 1024:
                self.cache.popitem(last=False)
            return target

    async def loop(self):
        while True:
            try:
                now = time.time()
                state = self.db.setting('sync')
                if state['enabled'] and now >= state['next_sync']:
                    self.catalog.start()
                if now >= self.next_probe:
                    self.next_probe = now + 300
                    self.spawn(self.catalog.probe())
                    self.db.cleanup()
                    # A daily/hourly health status is not inferred from merely having a cookie.
                    for b in self.db.all('SELECT b.user_id FROM bindings b JOIN users u ON u.id=b.user_id '
                                         'WHERE b.cookie IS NOT NULL AND u.disabled=0 AND COALESCE(b.checked,0)<? LIMIT 8', (now-300,)):
                        self.spawn(self.safe_check(b['user_id']))
                for task in self.db.all("SELECT t.id FROM tasks t JOIN users u ON u.id=t.user_id JOIN bindings b ON b.user_id=t.user_id "
                                        "WHERE u.disabled=0 AND t.revision=b.revision AND b.cookie IS NOT NULL "
                                        "AND t.state IN ('submitting','unknown','downloading') AND t.next_check<=? ORDER BY t.next_check LIMIT 16", (now,)):
                    if task['id'] not in self.active_tasks:
                        self.spawn(self.process_task(task['id']))
                for key, qr in list(self.qrs.items()):
                    if now > qr['expires'] + 60:
                        if qr.get('job'):
                            qr['job'].cancel()
                        self.qrs.pop(key, None)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Scheduler stays alive; do not log upstream data or secrets.
                import logging
                logging.getLogger('ltx').error('Background scheduling failed; retrying in 5 seconds')
            await asyncio.sleep(1)

    async def safe_check(self, uid):
        try:
            await self.check_binding(uid)
        except (HTTPException, UpstreamError):
            pass

    async def close(self):
        if self.catalog.job:
            self.catalog.job.cancel()
        for job in list(self.jobs):
            job.cancel()
        await asyncio.gather(*self.jobs, *([self.catalog.job] if self.catalog.job else []), return_exceptions=True)

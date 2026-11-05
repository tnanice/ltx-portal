import asyncio
import json
import time

import httpx


def validate_catalog(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('items'), list):
        raise ValueError('源站数据缺少 items 列表')
    codes = set()
    for row in payload['items']:
        if not isinstance(row, dict) or not isinstance(row.get('code'), str) or not row['code'].strip():
            raise ValueError('源站资源缺少有效编号')
        if row['code'] in codes:
            raise ValueError('源站包含重复资源编号')
        codes.add(row['code'])
        for key in ('links', 'actors'):
            if not isinstance(row.get(key), list) or not all(isinstance(v, str) for v in row[key]):
                raise ValueError(f'源站资源的 {key} 字段不兼容')
        for key in ('title', 'title_zh', 'maker', 'date', 'cover', 'shot', 'url'):
            if key in row and row[key] is not None and not isinstance(row[key], str):
                raise ValueError(f'源站资源的 {key} 字段不兼容')
    return payload


class Catalog:
    def __init__(self, db, config):
        self.db, self.config = db, config
        self.job = None
        self.lock = asyncio.Lock()
        state = db.setting('sync')
        state['running'] = False
        db.set('sync', state)

    def start(self):
        if not self.job or self.job.done():
            self.job = asyncio.create_task(self.sync())
        return self.job

    def data(self):
        row = self.db.one('SELECT payload FROM catalog WHERE id=1')
        return json.loads(row['payload']) if row else {'items': []}

    async def fetch(self, conditional=True):
        state = self.db.setting('sync')
        headers = {'User-Agent': 'LTX-Portal/1.0', 'Accept': 'application/json'}
        if conditional and self.db.one('SELECT id FROM catalog WHERE id=1'):
            if state['etag']:
                headers['If-None-Match'] = state['etag']
            if state['modified']:
                headers['If-Modified-Since'] = state['modified']
        async with asyncio.timeout(30):
            async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5), follow_redirects=True) as client:
                async with client.stream('GET', self.config.source, headers=headers) as response:
                    if response.status_code == 304:
                        return 304, None, dict(response.headers)
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 32 * 1024 * 1024:
                            raise ValueError('源站响应超过32MB限制')
        payload = validate_catalog(json.loads(body))
        if not payload['items'] and state.get('count', 0):
            raise ValueError('源站突然返回空目录，已保留原快照')
        return 200, payload, dict(response.headers)

    async def sync(self):
        async with self.lock:
            state = self.db.setting('sync')
            state.update(running=True, last_attempt=time.time())
            self.db.set('sync', state)
            try:
                code, payload, headers = await self.fetch()
                now = time.time()
                # Re-read to retain settings changed while the request was running.
                state = self.db.setting('sync')
                if code == 304 and not self.db.one('SELECT id FROM catalog WHERE id=1'):
                    raise ValueError('没有本地快照，不能接受304响应')
                if payload is not None:
                    state.update(count=len(payload['items']), etag=headers.get('etag', ''),
                                 modified=headers.get('last-modified', ''))
                state.update(last_success=now, last_error='', running=False,
                             next_sync=now + state['interval_seconds'])
                with self.db.connect() as c:
                    if payload is not None:
                        c.execute('INSERT INTO catalog VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                                  (json.dumps(payload, ensure_ascii=False),))
                    c.execute('UPDATE settings SET value=? WHERE key=?', (json.dumps(state), 'sync'))
                self.db.set('source_status', {'ok': True, 'checked': now, 'error': ''})
            except asyncio.CancelledError:
                raise
            except Exception as e:
                message = str(e) if isinstance(e, ValueError) else '源站不可达或返回HTTP错误，已保留原快照'
                state = self.db.setting('sync')
                state.update(last_error=message, running=False, next_sync=time.time() + state['interval_seconds'])
                self.db.set('sync', state)
                self.db.set('source_status', {'ok': False, 'checked': time.time(), 'error': message})
            finally:
                state = self.db.setting('sync')
                state['running'] = False
                self.db.set('sync', state)

    async def probe(self):
        if self.job and not self.job.done():
            return
        try:
            await self.fetch()
            result = {'ok': True, 'checked': time.time(), 'error': ''}
        except asyncio.CancelledError:
            raise
        except Exception:
            result = {'ok': False, 'checked': time.time(), 'error': '目录接口暂时不可用；本地快照仍可浏览'}
        self.db.set('source_status', result)

import asyncio
import json
import shutil
import time

import pytest
from fastapi.testclient import TestClient
from portal.config import Config
from portal.main import create_app
from portal.provider import Provider, UpstreamError, cookie_from_qr, link_identity
from portal.security import digest
from conftest import PASSWORD, LINK, FakeProvider, add_users, bind, completed, login


@pytest.mark.parametrize('path', ['/api/catalog/items', '/api/catalog/status', '/api/me/tasks', '/api/me/115', '/api/admin/users', '/api/players', '/api/playback/tickets'])
def test_anonymous_denied(env, path):
    assert env[1].get(path).status_code == 401


def test_auth_csrf_origin_roles(env):
    app, c, _ = env
    add_users(c)
    c.headers.pop('X-CSRF-Token')
    assert c.post('/api/auth/logout').status_code == 403
    login(c, 'alice')
    assert c.get('/api/admin/users').status_code == 403
    assert c.put('/api/admin/sync', json={'enabled': True, 'interval_seconds': 300}).status_code == 403
    assert c.post('/api/auth/logout', headers={'Origin': 'https://evil.test'}).status_code == 403
    assert c.post('/api/auth/logout').status_code == 200
    assert c.get('/api/auth/me').status_code == 401


def test_failed_binding_preserves_valid_and_encrypted(env):
    app, c, _ = env
    uid = login(c)
    bind(c)
    before = app.state.services.binding(uid)
    assert before['cookie'] != 'account=alice'
    assert c.post('/api/me/115/cookie', json={'cookie': 'invalid-cookie'}).status_code == 409
    assert c.post('/api/me/115/cookie', json={'cookie': 'timeout-cookie'}).status_code == 503
    assert app.state.services.binding(uid)['revision'] == before['revision']
    assert 'cookie' not in c.get('/api/me/115').json()


def test_task_and_ticket_isolation_dynamic_ua(env):
    app, c, fake = env
    add_users(c)
    alice = login(c, 'alice'); bind(c)
    tid = completed(app, c, alice)
    prepared = c.post('/api/playback/tickets', json={'task_id': tid, 'pickcode': 'file-1'})
    assert prepared.status_code == 200
    url = prepared.json()['url']
    for ua in ('IINA/1.4', 'VLC/3.0', 'PotPlayer/1.7', 'VidHub/3.0'):
        r = c.get(url, headers={'User-Agent': ua}, follow_redirects=False)
        assert r.status_code == 302 and r.headers['location'].startswith('https://cdn.example.test/')
        assert r.content == b''
        assert fake.calls[-1] == ('account=alice', 'file-1', ua)
    calls = len(fake.calls)
    assert c.head(url, headers={'User-Agent': 'VidHub/3.0'}, follow_redirects=False).status_code == 302
    assert len(fake.calls) == calls
    m3u = c.get(prepared.json()['m3u_url']).text
    assert url in m3u and 'token=' not in m3u and 'http-user-agent' not in m3u
    login(c, 'bob'); bind(c, 'account=bob')
    assert c.get('/api/me/tasks').json() == []
    assert c.get('/api/me/tasks/'+tid).status_code == 404
    assert c.post('/api/me/tasks/'+tid+'/retry').status_code == 404
    assert c.post('/api/playback/tickets', json={'task_id': tid, 'pickcode': 'file-1'}).status_code == 404
    assert c.get('/api/playback/tickets').json() == []


@pytest.mark.parametrize('operation', ['unbind', 'rebind', 'disable', 'password', 'revoke', 'expiry'])
def test_ticket_revocation(env, operation):
    app, c, fake = env
    add_users(c)
    uid = login(c, 'alice'); bind(c)
    tid = completed(app, c, uid)
    p = c.post('/api/playback/tickets', json={'task_id': tid, 'pickcode': 'file-1'}).json()
    if operation == 'unbind':
        assert c.delete('/api/me/115').status_code == 200
    elif operation == 'rebind':
        bind(c, 'account=new-account')
    elif operation == 'disable':
        login(c); assert c.patch('/api/admin/users/'+str(uid), json={'disabled': True}).status_code == 200
    elif operation == 'password':
        login(c); assert c.patch('/api/admin/users/'+str(uid), json={'password': 'changed-long-password'}).status_code == 200
    elif operation == 'revoke':
        ticket_id = c.get('/api/playback/tickets').json()[0]['id']
        assert c.delete('/api/playback/tickets/'+ticket_id).status_code == 200
    else:
        app.state.db.run('UPDATE tickets SET expires=0')
    assert c.get(p['url'], follow_redirects=False).status_code == 410
    assert not fake.deletes


def test_rejected_submission_is_not_success(env):
    app, c, fake = env
    login(c); bind(c)
    fake.submit_error = UpstreamError('rejected', '明确拒绝', 10010)
    row = c.post('/api/me/tasks', json={'code': 'TEST-001'}).json()
    for _ in range(30):
        t = c.get('/api/me/tasks/'+row['id']).json()
        if t['state'] == 'failed': break
        time.sleep(.01)
    assert t['state'] == 'failed' and t['error'] == '明确拒绝'
    c.post('/api/me/tasks', json={'code': 'TEST-001'})
    assert len([x for x in fake.calls if x[0] == 'submit']) == 1


def test_unknown_submission_reconciles_without_resubmit(env):
    app, c, fake = env
    uid = login(c); bind(c)
    fake.submit_error = UpstreamError('temporary', '超时')
    row = c.post('/api/me/tasks', json={'code': 'TEST-001'}).json()
    for _ in range(30):
        t = c.get('/api/me/tasks/'+row['id']).json()
        if t['state'] == 'unknown': break
        time.sleep(.01)
    assert t['state'] == 'unknown'
    fake.pages = {i: ([], 5) for i in range(1, 6)}
    fake.pages[5] = ([{'info_hash': '0123456789abcdef0123456789abcdef', 'status': 2}], 5)
    c.portal.call(app.state.services.process_task, row['id'])
    assert app.state.db.one('SELECT scan_page FROM tasks WHERE id=?', (row['id'],))['scan_page'] == 4
    c.portal.call(app.state.services.process_task, row['id'])
    t = c.get('/api/me/tasks/'+row['id']).json()
    assert t['state'] == 'completed' and len(t['files']) == 2
    assert len([x for x in fake.calls if x[0] == 'submit']) == 1


def test_timeout_is_read_only_and_missing_requires_explicit_retry(env):
    app, c, fake = env
    uid = login(c); bind(c); tid = completed(app, c, uid)
    fake.direct_error = UpstreamError('temporary', '超时')
    assert c.post('/api/playback/tickets', json={'task_id': tid, 'pickcode': 'file-1'}).status_code == 503
    assert c.get('/api/me/tasks/'+tid).json()['state'] == 'completed'
    fake.direct_error = UpstreamError('missing', '已删除')
    assert c.post('/api/playback/tickets', json={'task_id': tid, 'pickcode': 'file-1'}).status_code == 404
    assert fake.deletes == []
    assert c.post('/api/me/tasks/'+tid+'/retry').status_code == 200
    assert fake.deletes == ['test']


def test_qr_owner_cancel_success(env):
    app, c, fake = env
    add_users(c)
    login(c, 'alice')
    q = c.post('/api/me/115/qrcode', json={}).json()
    assert q['state'] == 'waiting'
    login(c, 'bob')
    assert c.get('/api/me/115/qrcode/'+q['id']).status_code == 404
    assert c.delete('/api/me/115/qrcode/'+q['id']).status_code == 404
    login(c, 'alice')
    assert c.delete('/api/me/115/qrcode/'+q['id']).status_code == 200
    fake.qr_states = [2]
    q = c.post('/api/me/115/qrcode', json={}).json()
    for _ in range(30):
        s = c.get('/api/me/115/qrcode/'+q['id']).json()
        if s['state'] == 'success': break
        time.sleep(.01)
    assert s['state'] == 'success'
    assert c.get('/api/me/115').json()['account_id'] == 'qr-user'


@pytest.mark.parametrize('qr_status,expected', [(-1, 'expired'), (-2, 'cancelled')])
def test_qr_terminal_states(env, qr_status, expected):
    app, c, fake = env
    login(c); fake.qr_states = [qr_status]
    q = c.post('/api/me/115/qrcode', json={}).json()
    for _ in range(20):
        state = c.get('/api/me/115/qrcode/'+q['id']).json()['state']
        if state == expected: break
        time.sleep(.01)
    assert state == expected


def test_login_rate_limit(env):
    c = env[1]
    for _ in range(10):
        assert c.post('/api/auth/login', json={'username': 'nobody', 'password': 'wrong'}).status_code == 401
    assert c.post('/api/auth/login', json={'username': 'nobody', 'password': 'wrong'}).status_code == 429


def test_nested_qr_cookie_and_link_validation():
    cookie = cookie_from_qr({'state': True, 'data': {'cookie': {'UID': 'u', 'CID': 'c', 'SEID': 's'}, 'user_id': 5}})
    assert cookie == 'UID=u; CID=c; SEID=s'
    with pytest.raises(UpstreamError): cookie_from_qr({'state': True, 'data': {'user_id': 5}})
    assert link_identity(LINK)[0].startswith('ed2k:')
    assert link_identity('magnet:?xt=urn:btih:'+'A'*32)[1] == '0'*40
    with pytest.raises(UpstreamError): link_identity('http://evil/'+ 'a'*40)


def test_copy_directory_preserves_state_and_keys(env, tmp_path):
    app, c, fake = env
    uid = login(c); bind(c); completed(app, c, uid)
    assert c.put('/api/me/favorites/TEST-001').status_code == 200
    assert c.put('/api/admin/sync', json={'enabled': False, 'interval_seconds': 7200}).status_code == 200
    dest = tmp_path / 'moved'
    # Online SQLite backup, matching backup.py. Offline whole-directory copy is also supported.
    dest.mkdir(); shutil.copytree(app.state.services.vault_path if hasattr(app.state.services,'vault_path') else app.state.db.path.parent/'secrets', dest/'secrets')
    import sqlite3
    with app.state.db.connect() as source:
        target = sqlite3.connect(dest/'portal.sqlite3'); source.backup(target); target.close()
    moved = create_app(Config(data=dest, admin_password='', background=False), FakeProvider())
    with TestClient(moved) as other:
        login(other)
        assert other.get('/api/me/115').json()['state'] == 'ok'
        assert len(other.get('/api/me/tasks').json()) == 1
        assert other.get('/api/me/favorites').json() == ['TEST-001']
        assert other.get('/api/catalog/status').json()['sync']['interval_seconds'] == 7200
        assert moved.state.services.cookie(moved.state.services.binding(uid)) == 'account=alice'

@pytest.mark.parametrize('state', ['submitting', 'downloading', 'unknown'])
def test_cancel_task_does_not_delete_files_and_is_idempotent(env, state):
    app, c, fake = env
    uid = login(c); bind(c)
    tid = completed(app, c, uid)
    app.state.db.run('UPDATE tasks SET state=? WHERE id=?', (state, tid))
    assert c.post('/api/me/tasks/'+tid+'/cancel').status_code == 200
    assert c.get('/api/me/tasks/'+tid).json()['state'] == 'cancelled'
    assert fake.deletes == ([] if state == 'submitting' else ['test'])
    assert fake.file_deletes == []
    assert c.post('/api/me/tasks/'+tid+'/cancel').status_code == 200
    # A worker queued before cancellation must not submit it afterwards.
    c.portal.call(app.state.services.process_task, tid)
    assert not any(call[0] == 'submit' for call in fake.calls)


def test_delete_completed_files_revokes_ticket_and_is_scoped(env):
    app, c, fake = env
    add_users(c)
    uid = login(c, 'alice'); bind(c)
    tid = completed(app, c, uid)
    url = c.post('/api/playback/tickets', json={'task_id':tid,'pickcode':'file-1'}).json()['url']
    login(c, 'bob'); bind(c, 'account=bob')
    assert c.delete('/api/me/tasks/'+tid).status_code == 404
    assert c.post('/api/me/tasks/'+tid+'/cancel').status_code == 404
    assert not fake.file_deletes
    login(c, 'alice')
    assert c.post('/api/me/tasks/'+tid+'/cancel').status_code == 409
    assert c.delete('/api/me/tasks/'+tid).status_code == 200
    assert fake.file_deletes == [('account=alice','test')]
    assert c.get('/api/me/tasks/'+tid).status_code == 404
    assert c.get(url, follow_redirects=False).status_code == 410


@pytest.mark.parametrize('operation,state', [('cancel','downloading'),('delete','completed')])
def test_delete_or_cancel_timeout_preserves_record(env, operation, state):
    app, c, fake = env
    uid = login(c); bind(c)
    tid = completed(app, c, uid)
    app.state.db.run('UPDATE tasks SET state=? WHERE id=?', (state, tid))
    fake.delete_error = UpstreamError('temporary', '上游请求超时，结果未知')
    r = c.post('/api/me/tasks/'+tid+'/cancel') if operation == 'cancel' else c.delete('/api/me/tasks/'+tid)
    assert r.status_code == 503
    assert c.get('/api/me/tasks/'+tid).json()['state'] == state


def test_prepare_does_not_recreate_ticket_after_concurrent_delete(env):
    app, c, fake = env
    uid = login(c); bind(c)
    tid = completed(app, c, uid)
    async def direct_then_deleted(cookie, pick, ua):
        app.state.db.run('DELETE FROM tasks WHERE id=?', (tid,))
        return 'https://cdn.example.test/video', time.time()+60
    fake.direct = direct_then_deleted
    assert c.post('/api/playback/tickets', json={'task_id':tid,'pickcode':'file-1'}).status_code == 404
    assert c.get('/api/playback/tickets').json() == []


def test_instant_offline_reconciles_in_the_submission_pass(env):
    app, c, fake = env
    uid = login(c); bind(c); tid = completed(app, c, uid)
    app.state.db.run("UPDATE tasks SET state='submitting',files='[]' WHERE id=?", (tid,))
    fake.pages = {1: ([{'info_hash': '0123456789abcdef0123456789abcdef', 'status': 2}], 1)}
    c.portal.call(app.state.services.process_task, tid)
    assert c.get('/api/me/tasks/'+tid).json()['state'] == 'completed'


def test_submit_completion_hint_does_not_wait_for_task_listing(env):
    app, c, fake = env
    uid = login(c); bind(c); tid = completed(app, c, uid)
    app.state.db.run("UPDATE tasks SET state='submitting',files='[]' WHERE id=?", (tid,))
    async def submit(*args): return {'info_hash': 'test', 'task': {'pick_code': 'ready'}}
    async def no_list(*args): raise AssertionError('Completion hint should not wait for a list refresh')
    fake.submit = submit;fake.task_page = no_list
    c.portal.call(app.state.services.process_task, tid)
    assert c.get('/api/me/tasks/'+tid).json()['state'] == 'completed'


def test_upstream_99_percent_is_not_reported_as_completed(env):
    app, c, fake = env
    uid = login(c); bind(c); tid = completed(app, c, uid)
    app.state.db.run("UPDATE tasks SET state='downloading',files='[]' WHERE id=?", (tid,))
    fake.pages = {1: ([{'info_hash': 'test', 'status': 1, 'percentDone': 99}], 1)}
    c.portal.call(app.state.services.process_task, tid)
    row = app.state.db.one('SELECT * FROM tasks WHERE id=?', (tid,))
    assert row['state'] == 'downloading' and row['percent'] == 99
    assert row['next_check'] - time.time() <= 2.1


def test_slow_task_does_not_block_another_task_for_the_same_user(env):
    app, c, fake = env
    uid = login(c); bind(c); tid = completed(app, c, uid)
    app.state.db.run("UPDATE tasks SET state='downloading' WHERE id=?", (tid,))
    app.state.db.run("INSERT INTO tasks(id,user_id,revision,resource,title,link,link_key,info_hash,state,created,updated) "
                     "SELECT 'fast-task',user_id,revision,resource,title,link,'different-key',info_hash,'downloading',created,updated FROM tasks WHERE id=?", (tid,))
    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0
        async def task_page(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set(); await release.wait()
            return ([{'info_hash': 'test', 'status': 2}], 1)
        fake.task_page = task_page
        slow = asyncio.create_task(app.state.services.process_task(tid))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            await asyncio.wait_for(app.state.services.process_task('fast-task'), 1)
            assert not slow.done()
            assert app.state.db.one("SELECT state FROM tasks WHERE id='fast-task'")['state'] == 'completed'
        finally:
            release.set(); await slow
    c.portal.call(exercise)


def test_pasted_ed2k_submission_and_catalog_dedup(env):
    app, c, fake = env
    login(c); bind(c)
    pasted = c.post('/api/me/tasks', json={'link': '  '+LINK+'\n'})
    assert pasted.status_code == 202
    assert pasted.json()['resource'] == 'TEST-001'
    existing = c.post('/api/me/tasks', json={'code': 'TEST-001'})
    assert existing.json()['id'] == pasted.json()['id']
    custom = LINK.replace('example.mp4', '%E6%B5%8B%E8%AF%95.mp4').replace('0123456789abcdef0123456789abcdef', 'a'*32)
    result = c.post('/api/me/tasks', json={'link': custom})
    assert result.status_code == 202
    assert result.json()['title'] == '测试.mp4'
    assert result.json()['resource'].startswith('ED2K-')
    assert len(c.get('/api/me/tasks').json()) == 2


@pytest.mark.parametrize('payload', [{}, {'code':'TEST-001','link':LINK}, {'link':'https://example.com/video'}, {'link':LINK+'\n'+LINK}, {'link':LINK[:-1]}])
def test_invalid_pasted_link_never_submits(env, payload):
    app, c, fake = env
    login(c); bind(c)
    assert c.post('/api/me/tasks', json=payload).status_code == 422
    assert c.get('/api/me/tasks').json() == []
    assert not fake.calls

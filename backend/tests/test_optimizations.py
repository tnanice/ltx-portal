import gc
import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from portal.config import Config
from portal.main import create_app
from portal.provider import Provider
from conftest import FakeProvider, PASSWORD, add_users, bind, completed, login


def test_catalog_index_avoids_rereading_payload_until_updated(env):
    app, c, _ = env
    login(c)
    cat = app.state.catalog
    assert cat.by_code('TEST-001')['code'] == 'TEST-001'
    assert cat.by_normalized_code('test001')['code'] == 'TEST-001'

    payload_reads = 0
    orig_one = app.state.db.one

    def spy_one(sql, args=()):
        nonlocal payload_reads
        if 'FROM catalog' in sql:
            payload_reads += 1
        return orig_one(sql, args)

    app.state.db.one = spy_one
    try:
        assert cat.by_code('TEST-001')['code'] == 'TEST-001'
        assert cat.by_normalized_code('test_001')['code'] == 'TEST-001'
        r1 = c.get('/api/catalog/items')
        r2 = c.get('/api/catalog/items', headers={'If-None-Match': r1.headers['etag']})
        assert r1.status_code == 200 and r2.status_code == 304
        assert payload_reads == 0

        updated = r1.json()
        updated['items'][0]['title'] = 'Trigger Refreshed'
        app.state.db.run('UPDATE catalog SET payload=? WHERE id=1', (json.dumps(updated),))
        assert cat.by_code('TEST-001')['title'] == 'Trigger Refreshed'
        assert payload_reads == 1
    finally:
        app.state.db.one = orig_one


def test_task_locks_are_weakly_referenced(env):
    app, _, _ = env
    svc = app.state.services
    lock = svc.task_lock('ephemeral-task-id')
    assert 'ephemeral-task-id' in svc.task_locks
    del lock
    gc.collect()
    assert 'ephemeral-task-id' not in svc.task_locks


def test_direct_cache_updates_lru_order(env):
    app, c, _ = env
    uid = login(c)
    bind(c)
    tid = completed(app, c, uid)
    prepared = c.post('/api/playback/tickets', json={'task_id': tid, 'pickcode': 'file-1'}).json()
    url = prepared['url']
    assert c.get(url, headers={'User-Agent': 'PlayerA'}, follow_redirects=False).status_code == 302
    assert c.get(url, headers={'User-Agent': 'PlayerB'}, follow_redirects=False).status_code == 302
    assert list(app.state.services.cache.keys())[-1][3] == 'PlayerB'
    assert c.get(url, headers={'User-Agent': 'PlayerA'}, follow_redirects=False).status_code == 302
    assert list(app.state.services.cache.keys())[-1][3] == 'PlayerA'


def test_login_rate_limit_scoped_per_user_and_trusted_proxy(tmp_path):
    cfg = Config(data=tmp_path, admin_password=PASSWORD, background=False, trusted_proxies={'testclient'})
    app = create_app(cfg, FakeProvider())
    with TestClient(app) as c:
        add_users(c)
        c.post('/api/auth/logout')
        for _ in range(10):
            assert c.post('/api/auth/login', json={'username': 'nobody', 'password': 'wrong'}).status_code == 401
        assert c.post('/api/auth/login', json={'username': 'nobody', 'password': 'wrong'}).status_code == 429
        # Different user from same proxy IP is not locked out by single-account brute force.
        assert c.post('/api/auth/login', json={'username': 'alice', 'password': PASSWORD}).status_code == 200
        # Trusted proxy X-Real-IP separates different real clients for the same username.
        assert c.post('/api/auth/login', json={'username': 'nobody', 'password': 'wrong'},
                      headers={'X-Real-IP': '203.0.113.5'}).status_code == 401


def test_provider_client_cache_reuse_and_drop():
    p = Provider()
    with patch('portal.provider.P115Client', side_effect=lambda ck: object()) as mock_cls:
        c1 = p.client('UID=1; CID=2; SEID=3')
        c2 = p.client('UID=1; CID=2; SEID=3')
        assert c1 is c2
        assert mock_cls.call_count == 1
        p.drop_client('UID=1; CID=2; SEID=3')
        c3 = p.client('UID=1; CID=2; SEID=3')
        assert c3 is not c1
        assert mock_cls.call_count == 2

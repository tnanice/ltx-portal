import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from portal.config import Config
from portal.main import create_app
from portal.provider import UpstreamError

PASSWORD = 'a-secure-test-password'
LINK = 'ed2k://|file|example.mp4|123|0123456789abcdef0123456789abcdef|/'
CATALOG = {'items': [{'code': 'TEST-001', 'title': '测试作品', 'actors': ['示例'], 'links': [LINK]}], 'updated_at': 1}


class FakeProvider:
    def __init__(self):
        self.calls = []
        self.submit_error = None
        self.direct_error = None
        self.pages = {1: ([], 1)}
        self.qr_states = []
        self.deletes = []
        self.file_deletes = []
        self.delete_error = None

    async def verify(self, cookie):
        if cookie == 'invalid-cookie':
            raise UpstreamError('invalid', '无效凭据')
        if cookie == 'timeout-cookie':
            raise UpstreamError('temporary', '暂不可达')
        return cookie.split('=')[-1]

    async def direct(self, cookie, pick, ua):
        self.calls.append((cookie, pick, ua))
        if self.direct_error:
            raise self.direct_error
        return 'https://cdn.example.test/video?f=1&t=9999999999', time.time()+600

    async def submit(self, cookie, link, save_path=''):
        self.calls.append(('submit', cookie, link))
        if self.submit_error:
            raise self.submit_error
        return {'duplicate': False, 'info_hash': '0123456789abcdef0123456789abcdef'}

    async def task_page(self, cookie, page):
        return self.pages.get(page, ([], page))

    async def files(self, cookie, task):
        return [{'pickcode': 'file-1', 'name': '视频一.mp4', 'size': 100}, {'pickcode': 'file-2', 'name': '视频二.mkv', 'size': 200}]

    async def remove_record(self, cookie, info_hash):
        if self.delete_error:
            raise self.delete_error
        self.deletes.append(info_hash)

    async def remove_task_files(self, cookie, info_hash):
        if self.delete_error:
            raise self.delete_error
        self.file_deletes.append((cookie, info_hash))

    async def qr_new(self, app):
        return {'uid': 'fake', 'sign': 'fake', 'time': 1}, 'data:image/png;base64,fake'

    async def qr_poll(self, token):
        if self.qr_states:
            return self.qr_states.pop(0)
        await asyncio.sleep(3600)
        return 0

    async def qr_result(self, token, app):
        return 'account=qr-user'


@pytest.fixture
def env(tmp_path):
    provider = FakeProvider()
    app = create_app(Config(data=tmp_path, admin_password=PASSWORD, background=False), provider)
    with TestClient(app) as client:
        app.state.db.run('INSERT INTO catalog VALUES(1,?)', (json.dumps(CATALOG),))
        yield app, client, provider


def login(client, name='admin'):
    r = client.post('/api/auth/login', json={'username': name, 'password': PASSWORD})
    assert r.status_code == 200, r.text
    client.headers['X-CSRF-Token'] = r.json()['csrf']
    return r.json()['user']['id']


def add_users(client):
    login(client)
    for name in ('alice', 'bob'):
        assert client.post('/api/admin/users', json={'username': name, 'password': PASSWORD}).status_code == 200


def bind(client, account='account=alice'):
    r = client.post('/api/me/115/cookie', json={'cookie': account})
    assert r.status_code == 200, r.text


def completed(app, client, uid):
    b = app.state.services.binding(uid)
    tid = 'task-'+str(uid)
    now = time.time()
    app.state.db.run('INSERT INTO tasks(id,user_id,revision,resource,title,link,link_key,info_hash,state,files,created,updated) '
                     'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (tid, uid, b['revision'], 'TEST-001', '测试', LINK, 'ed2k:test', 'test', 'completed',
                      json.dumps([{'pickcode': 'file-1', 'name': '视频一.mp4', 'size': 100}]), now, now))
    return tid

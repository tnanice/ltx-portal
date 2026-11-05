import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from portal.provider import Provider, UpstreamError

COOKIE = 'UID=1; CID=c; SEID=s'
LINK = 'ed2k://|file|sample.mp4|123|0123456789abcdef0123456789abcdef|/'


async def test_actual_submit_parser_rejects_known_hash_failure():
    p = Provider()
    p.client = lambda _: SimpleNamespace(clouddownload_task_add_url='submit')
    p.call = AsyncMock(return_value={'state': False, 'errcode': 10010, 'error_msg': 'Rejected'})
    with pytest.raises(UpstreamError) as e:
        await p.submit(COOKIE, LINK)
    assert e.value.kind == 'rejected'
    p.call.return_value = {'state': False, 'errcode': 10008}
    assert (await p.submit(COOKIE, LINK))['duplicate'] is True
    p.call.return_value = {'state': True, 'data': {}}
    assert (await p.submit(COOKIE, LINK))['duplicate'] is False


class URL(str):
    headers = {}


async def test_no_cookie_dependent_direct_fallback():
    p = Provider(); p.client = lambda _: SimpleNamespace(download_url='download')
    p.call = AsyncMock(return_value=URL('https://cdn.test/video?f=3&t=9999999999'))
    with pytest.raises(UpstreamError) as e:
        await p.direct(COOKIE, 'pick', 'IINA')
    assert e.value.kind == 'unsupported'
    u = URL('https://cdn.test/video?f=1&t=9999999999'); u.headers = {'cookie': 'secret'}
    p.call.return_value = u
    with pytest.raises(UpstreamError): await p.direct(COOKIE, 'pick', 'IINA')


async def test_directory_pickcode_not_deleted():
    p = Provider()
    p.client = lambda _: SimpleNamespace(fs_file='metadata', download_url='direct', fs_files_app='list')
    async def call(fn, *args, **kwargs):
        if fn == 'metadata': return {'state': True, 'data': [{'fc': '0', 'pc': 'directory'}]}
        if fn == 'direct': raise AssertionError('Directory must not be requested as a direct link')
        return {'state': True, 'data': [{'fid': '2', 'n': 'a.mp4', 'pc': 'one'}, {'fid': '3', 'n': 'b.mkv', 'pc': 'two'}, {'fid': '4', 'n': 'readme.txt', 'pc': 'no'}], 'count': 3}
    p.call = call
    files = await p.files(COOKIE, {'file_id': '1', 'pick_code': 'directory'})
    assert [f['pickcode'] for f in files] == ['one', 'two']


async def test_timeout_cancels_native_request_and_disables_retry():
    p = Provider(); cancelled = False
    async def never(**kwargs):
        nonlocal cancelled
        assert kwargs['async_'] is True and kwargs['retries'] == 0
        try: await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled = True
            raise
    with pytest.raises(UpstreamError): await p.call(never, timeout=.02)
    assert cancelled

async def test_cancel_keeps_files_delete_removes_only_requested_task():
    p = Provider(); p.client = lambda _: SimpleNamespace(clouddownload_task_del='delete')
    p.call = AsyncMock(return_value={'state': True})
    await p.remove_record(COOKIE, 'task-hash')
    p.call.assert_awaited_with('delete', {'hash[0]': 'task-hash', 'flag': 0})
    await p.remove_task_files(COOKIE, 'task-hash')
    p.call.assert_awaited_with('delete', {'hash[0]': 'task-hash', 'flag': 1})
    p.call.return_value = {'state': False, 'errcode': 10010}
    with pytest.raises(UpstreamError):
        await p.remove_task_files(COOKIE, 'task-hash')

async def test_task_file_pick_is_not_replaced_by_shared_parent_contents():
    p = Provider()
    p.client = lambda _: SimpleNamespace(fs_file='metadata', download_url='direct', fs_files_app='list')
    async def call(fn, *args, **kwargs):
        if fn == 'metadata': return {'state': True, 'data': [{'fc': '0', 'pc': 'parent-folder'}]}
        if fn == 'list': raise AssertionError('Must not list unrelated files in the shared parent')
        u = URL('https://cdn.test/file');u.name = 'correct.mp4';u.size = 123
        return u
    p.call = call
    files = await p.files(COOKIE, {'file_id': 'shared-parent', 'pick_code': 'this-file'})
    assert files == [{'pickcode': 'this-file', 'name': 'correct.mp4', 'size': 123}]

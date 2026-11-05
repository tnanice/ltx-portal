import asyncio
import json

import pytest
from portal.catalog import Catalog, validate_catalog
from portal.config import Config
from portal.db import DB
from conftest import CATALOG


@pytest.mark.parametrize('data', [{}, {'items': None}, {'items': ['oops']}, {'items': [{'code': 'x', 'links': [], 'actors': 'x'}]}, {'items': [CATALOG['items'][0], CATALOG['items'][0]]}])
def test_reject_bad_shape(data):
    with pytest.raises(ValueError): validate_catalog(data)


async def test_sync_success_304_failure_and_dedupe(tmp_path):
    db = DB(tmp_path)
    catalog = Catalog(db, Config(data=tmp_path))
    calls = 0
    async def good():
        nonlocal calls
        calls += 1
        await asyncio.sleep(.01)
        return 200, CATALOG, {'etag': 'v1'}
    catalog.fetch = good
    first = catalog.start()
    assert catalog.start() is first
    await first
    assert calls == 1 and catalog.data() == CATALOG
    async def not_modified(): return 304, None, {}
    catalog.fetch = not_modified
    await catalog.sync()
    assert catalog.data() == CATALOG and not db.setting('sync')['last_error']
    success = db.setting('sync')['last_success']
    async def fail(): raise ValueError('格式异常')
    catalog.fetch = fail
    await catalog.sync()
    assert catalog.data() == CATALOG
    assert db.setting('sync')['last_success'] == success
    assert db.setting('sync')['last_error'] == '格式异常'
    assert db.setting('source_status')['ok'] is False


async def test_304_without_snapshot_is_failure(tmp_path):
    db = DB(tmp_path); catalog = Catalog(db, Config(data=tmp_path))
    async def not_modified(): return 304, None, {}
    catalog.fetch = not_modified
    await catalog.sync()
    assert db.setting('sync')['last_success'] is None


async def test_settings_changed_during_sync_are_retained(tmp_path):
    db = DB(tmp_path); catalog = Catalog(db, Config(data=tmp_path))
    async def fetch():
        s=db.setting('sync');s.update(enabled=False,interval_seconds=1800);db.set('sync',s)
        return 200,CATALOG,{}
    catalog.fetch=fetch
    await catalog.sync()
    assert db.setting('sync')['enabled'] is False
    assert db.setting('sync')['interval_seconds'] == 1800

import asyncio
import sqlite3
import time
from portal.db import DB
from conftest import LINK, bind, login


def test_existing_database_migration(tmp_path):
    DB(tmp_path)
    with sqlite3.connect(tmp_path / 'portal.sqlite3') as c:
        for column in ('download_rate', 'last_progress_at', 'speed_checked_at'):
            c.execute(f'ALTER TABLE tasks DROP COLUMN {column}')
    DB(tmp_path)
    DB(tmp_path)
    with sqlite3.connect(tmp_path / 'portal.sqlite3') as c:
        assert {'download_rate', 'last_progress_at', 'speed_checked_at'} <= {r[1] for r in c.execute('PRAGMA table_info(tasks)')}


def test_progress_stall_recovery_and_stale_speed(env):
    app, client, fake = env
    login(client)
    bind(client)
    task = client.post('/api/me/tasks', json={'link': LINK}).json()
    db, svc = app.state.db, app.state.services
    row = db.one('SELECT * FROM tasks WHERE id=?', (task['id'],))
    sample = {'info_hash': row['info_hash'], 'status': 1, 'percentDone': 99, 'rateDownload': 2048}
    fake.pages = {1: ([sample], 1)}
    db.run("UPDATE tasks SET state='downloading' WHERE id=?", (task['id'],))
    asyncio.run(svc.process_task(task['id']))
    view = client.get('/api/me/tasks').json()[0]
    assert view['download_rate'] == 2048
    assert view['stalled_seconds'] < 5
    start = time.time() - 650
    db.run('UPDATE tasks SET last_progress_at=? WHERE id=?', (start, task['id']))
    sample['rateDownload'] = 0
    asyncio.run(svc.process_task(task['id']))
    view = client.get('/api/me/tasks').json()[0]
    assert view['stalled_seconds'] >= 650 and view['download_rate'] == 0
    db.run('UPDATE tasks SET speed_checked_at=? WHERE id=?', (time.time()-120, task['id']))
    view = client.get('/api/me/tasks').json()[0]
    assert view['telemetry_stale'] and view['download_rate'] is None
    sample['percentDone'] = 99.5
    asyncio.run(svc.process_task(task['id']))
    assert client.get('/api/me/tasks').json()[0]['stalled_seconds'] < 5
    sample.update(status=2, percentDone=100)
    asyncio.run(svc.process_task(task['id']))
    view = client.get('/api/me/tasks').json()[0]
    assert view['state'] == 'completed' and view['stalled_seconds'] == 0 and view['download_rate'] is None

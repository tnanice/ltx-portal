import json
from conftest import login, bind, completed, add_users


def test_offline_library_joins_metadata_and_preserves_unmatched_files(env):
    app, c, _ = env
    uid = login(c)
    bind(c)
    tid = completed(app, c, uid)
    payload = {'items': [{'code': 'TEST-001', 'title': 'Movie', 'title_zh': '影片',
                          'cover': 'https://images.example/cover.jpg', 'actors': ['Actor'],
                          'date': '2026-01-01', 'links': ['private-source-link']}]}
    app.state.db.run('UPDATE catalog SET payload=? WHERE id=1', (json.dumps(payload),))
    response = c.get('/api/me/library')
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    movie = response.json()[0]
    assert movie['id'] == tid and movie['files'][0]['pickcode'] == 'file-1'
    assert movie['metadata']['cover'] == payload['items'][0]['cover']
    assert movie['metadata']['title_zh'] == '影片'
    assert 'links' not in movie['metadata'] and 'link' not in movie
    app.state.db.run('UPDATE tasks SET resource=? WHERE id=?', ('manual-file', tid))
    movie = c.get('/api/me/library').json()[0]
    assert movie['metadata'] is None and movie['files']
    app.state.db.run("UPDATE tasks SET state='downloading' WHERE id=?", (tid,))
    assert c.get('/api/me/library').json() == []


def test_offline_library_is_bound_to_account_and_revision(env):
    app, c, _ = env
    add_users(c)
    uid = login(c, 'alice')
    bind(c)
    completed(app, c, uid)
    login(c, 'bob')
    bind(c, 'account=bob')
    assert c.get('/api/me/library').json() == []
    login(c, 'alice')
    bind(c, 'account=alice-new-binding')
    assert c.get('/api/me/library').json() == []
    c.post('/api/auth/logout')
    assert c.get('/api/me/library').status_code == 401

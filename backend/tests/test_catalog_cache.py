import json
from conftest import login


def test_conditional_catalog_is_authenticated_and_updates(env):
    app, c, _ = env
    login(c)
    first = c.get('/api/catalog/items')
    assert first.status_code == 200
    assert first.headers['cache-control'] == 'private, no-cache'
    assert first.headers['content-encoding'] == 'gzip'
    tag = first.headers['etag']
    hit = c.get('/api/catalog/items', headers={'If-None-Match': tag})
    assert hit.status_code == 304 and hit.content == b''
    assert hit.headers['etag'] == tag
    identity = c.get('/api/catalog/items', headers={'Accept-Encoding': 'gzip;q=0, identity'})
    assert 'content-encoding' not in identity.headers
    assert identity.json() == first.json()
    changed = first.json()
    changed['items'][0]['title'] = 'new title'
    app.state.db.run('UPDATE catalog SET payload=? WHERE id=1', (json.dumps(changed),))
    update = c.get('/api/catalog/items', headers={'If-None-Match': tag})
    assert update.status_code == 200 and update.headers['etag'] != tag
    assert update.json()['items'][0]['title'] == 'new title'
    c.post('/api/auth/logout')
    rejected = c.get('/api/catalog/items', headers={'If-None-Match': update.headers['etag']})
    assert rejected.status_code == 401 and rejected.headers['cache-control'] == 'no-store'

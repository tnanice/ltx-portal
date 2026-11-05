import asyncio
import json
import re
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .catalog import Catalog
from .config import Config
from .db import DB
from .provider import Provider, UpstreamError, link_identity
from .security import DUMMY_HASH, Limiter, Vault, admin, digest, password_ok, passwords, public_user, user
from .services import Services


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class NewUser(Login):
    password: str = Field(min_length=12, max_length=256)


class ChangePassword(BaseModel):
    old_password: str = Field(max_length=256)
    password: str = Field(min_length=12, max_length=256)


class UserChange(BaseModel):
    disabled: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=256)


class SyncSettings(BaseModel):
    enabled: bool
    interval_seconds: int = Field(ge=300, le=604800)


class CookieInput(BaseModel):
    cookie: str = Field(min_length=10, max_length=16384)


class QRInput(BaseModel):
    app: str = 'os_windows'


class TaskInput(BaseModel):
    code: str = Field(min_length=1, max_length=128)
    link_index: int = Field(default=0, ge=0)


class TicketInput(BaseModel):
    task_id: str
    pickcode: str = Field(min_length=1, max_length=128)


class SavePath(BaseModel):
    save_path: str = Field(default='', pattern=r'^\d{0,30}$')


PLAYERS = [
    {'id': 'potplayer', 'name': 'PotPlayer', 'platforms': ['windows']},
    {'id': 'iina', 'name': 'IINA', 'platforms': ['mac']},
    {'id': 'vlc-ios', 'name': 'VLC', 'platforms': ['ios']},
    {'id': 'vlc-android', 'name': 'VLC', 'platforms': ['android']},
    {'id': 'vidhub', 'name': 'VidHub', 'platforms': ['ios', 'android', 'mac']},
    {'id': 'm3u', 'name': '下载 M3U', 'platforms': ['all']},
]


def create_app(config=None, provider=None):
    cfg = config or Config()
    db = DB(cfg.data)
    if db.one('SELECT user_id FROM bindings WHERE cookie IS NOT NULL LIMIT 1') and not (cfg.data / 'secrets/master.key').exists():
        raise RuntimeError('缺少凭据加密密钥；请从完整备份恢复 data/secrets，不能生成新密钥替代')
    vault = Vault(cfg.data / 'secrets')
    catalog = Catalog(db, cfg)
    svc = Services(db, vault, provider or Provider(), catalog)
    limit = Limiter()

    @asynccontextmanager
    async def lifespan(app):
        if not db.one("SELECT id FROM users WHERE role='admin'"):
            if len(cfg.admin_password) < 12:
                raise RuntimeError('首次启动必须配置至少12位的 ADMIN_PASSWORD，不提供默认密码')
            db.run('INSERT INTO users(username,password,role,created) VALUES(?,?,?,?)',
                   (cfg.admin_user, passwords.hash(cfg.admin_password), 'admin', time.time()))
        loop = asyncio.create_task(svc.loop()) if cfg.background else None
        yield
        if loop:
            loop.cancel()
            await asyncio.gather(loop, return_exceptions=True)
        await svc.close()

    app = FastAPI(title='LTX Portal', version='1.0.0', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db, app.state.services, app.state.catalog = db, svc, catalog
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=cfg.allowed_hosts)

    @app.middleware('http')
    async def boundaries(request, call_next):
        # Same-origin only, including login (login CSRF). No wildcard CORS.
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            expected = cfg.public_url or str(request.base_url).rstrip('/')
            if origin and origin.rstrip('/') != expected:
                return JSONResponse({'detail': '不允许跨站写入'}, status_code=403)
        try:
            length = int(request.headers.get('content-length', '0') or 0)
        except ValueError:
            return JSONResponse({'detail': '无效请求长度'}, status_code=400)
        if length > 65536:
            return JSONResponse({'detail': '请求过大'}, status_code=413)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' https: http: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.url.path.startswith(('/api/', '/play/')):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(UpstreamError)
    async def upstream_handler(request, exc):
        code = {'invalid': 409, 'rejected': 422, 'missing': 404, 'unsupported': 422, 'directory': 409}.get(exc.kind, 503)
        return JSONResponse({'detail': exc.message, 'kind': exc.kind}, status_code=code)

    @app.get('/healthz')
    async def health():
        db.one('SELECT 1')
        return {'ok': True, 'version': '1.0.0'}

    @app.post('/api/auth/login')
    async def login(body: Login, request: Request, response: Response):
        limit.check(('login', request.client.host if request.client else '?'), 10, 300)
        u = db.one('SELECT * FROM users WHERE username=?', (body.username,))
        valid = await asyncio.to_thread(password_ok, u['password'] if u else DUMMY_HASH, body.password)
        latest = db.one('SELECT * FROM users WHERE username=?', (body.username,))
        if not valid or not u or not latest or latest['disabled'] or latest['password'] != u['password']:
            raise HTTPException(401, '账号或密码错误')
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        db.run('INSERT INTO sessions VALUES(?,?,?,?)', (digest(token), u['id'], csrf, time.time()+cfg.session_ttl))
        response.set_cookie('ltx_session', token, httponly=True, secure=cfg.secure_cookie,
                            samesite='lax', max_age=cfg.session_ttl, path='/')
        return {'user': public_user(u), 'csrf': csrf}

    @app.get('/api/auth/me')
    async def me(u=Depends(user)):
        return {'user': public_user(u), 'csrf': u['csrf']}

    @app.post('/api/auth/logout')
    async def logout(response: Response, u=Depends(user)):
        db.run('DELETE FROM sessions WHERE token=?', (u['session_token'],))
        response.delete_cookie('ltx_session', path='/')
        return {'ok': True}

    @app.post('/api/auth/password')
    async def change_password(body: ChangePassword, response: Response, u=Depends(user)):
        limit.check(('password', u['id']), 5, 300)
        if not await asyncio.to_thread(password_ok, u['password'], body.old_password):
            raise HTTPException(400, '原密码不正确')
        encoded = await asyncio.to_thread(passwords.hash, body.password)
        with db.connect() as c:
            c.execute('UPDATE users SET password=? WHERE id=?', (encoded, u['id']))
            c.execute('DELETE FROM sessions WHERE user_id=?', (u['id'],))
        svc.revoke(u['id'])
        response.delete_cookie('ltx_session', path='/')
        return {'ok': True}

    @app.get('/api/admin/users')
    async def list_users(u=Depends(admin)):
        return db.all('SELECT id,username,role,disabled,created FROM users ORDER BY id')

    @app.post('/api/admin/users')
    async def add_user(body: NewUser, u=Depends(admin)):
        if not re.fullmatch(r'[\w.@-]{1,64}', body.username):
            raise HTTPException(422, '账号仅支持文字、数字和 . @ _ -')
        encoded = await asyncio.to_thread(passwords.hash, body.password)
        try:
            uid = db.run('INSERT INTO users(username,password,created) VALUES(?,?,?)', (body.username, encoded, time.time()))
        except sqlite3.IntegrityError:
            raise HTTPException(409, '账号已存在') from None
        return {'id': uid, 'username': body.username}

    @app.patch('/api/admin/users/{uid}')
    async def edit_user(uid: int, body: UserChange, u=Depends(admin)):
        target = db.one('SELECT * FROM users WHERE id=?', (uid,))
        if not target:
            raise HTTPException(404)
        if uid == u['id'] and body.disabled:
            raise HTTPException(409, '不能禁用当前管理员')
        encoded = await asyncio.to_thread(passwords.hash, body.password) if body.password else None
        with db.connect() as c:
            if encoded:
                c.execute('UPDATE users SET password=? WHERE id=?', (encoded, uid))
            if body.disabled is not None:
                c.execute('UPDATE users SET disabled=? WHERE id=?', (int(body.disabled), uid))
            if encoded or body.disabled:
                c.execute('DELETE FROM sessions WHERE user_id=?', (uid,))
        if encoded or body.disabled:
            svc.revoke(uid)
        return {'ok': True}

    @app.get('/api/catalog/items')
    async def items(u=Depends(user)):
        return catalog.data()

    @app.get('/api/catalog/status')
    async def status(u=Depends(user)):
        return {'sync': db.setting('sync'), 'source': db.setting('source_status', {'ok': None, 'checked': None, 'error': ''})}

    @app.put('/api/admin/sync')
    async def set_sync(body: SyncSettings, u=Depends(admin)):
        state = db.setting('sync')
        state.update(enabled=body.enabled, interval_seconds=body.interval_seconds, next_sync=time.time()+body.interval_seconds)
        db.set('sync', state)
        return state

    @app.post('/api/admin/sync-now', status_code=202)
    async def sync_now(u=Depends(admin)):
        limit.check(('sync', u['id']), 6)
        catalog.start()
        return {'ok': True, 'running': True}

    @app.get('/api/me/favorites')
    async def favorites(u=Depends(user)):
        return [r['code'] for r in db.all('SELECT code FROM favorites WHERE user_id=?', (u['id'],))]

    @app.put('/api/me/favorites/{code}')
    async def add_favorite(code: str, u=Depends(user)):
        if len(code) > 128 or not any(r['code'] == code for r in catalog.data()['items']):
            raise HTTPException(404, '资源不存在')
        db.run('INSERT OR IGNORE INTO favorites VALUES(?,?)', (u['id'], code))
        return {'ok': True}

    @app.delete('/api/me/favorites/{code}')
    async def del_favorite(code: str, u=Depends(user)):
        db.run('DELETE FROM favorites WHERE user_id=? AND code=?', (u['id'], code))
        return {'ok': True}

    @app.get('/api/me/115')
    async def binding_status(u=Depends(user)):
        b = db.one('SELECT account_id,state,checked,save_path,revision FROM bindings WHERE user_id=?', (u['id'],))
        return b or {'state': 'unbound', 'account_id': None, 'checked': None, 'save_path': '', 'revision': 0}

    @app.post('/api/me/115/check')
    async def check_binding(u=Depends(user)):
        limit.check(('binding-check', u['id']), 6)
        return {'state': await svc.check_binding(u['id'])}

    @app.post('/api/me/115/cookie')
    async def bind_cookie(body: CookieInput, u=Depends(user)):
        limit.check(('binding', u['id']), 6)
        b = db.one('SELECT revision FROM bindings WHERE user_id=?', (u['id'],))
        result = await svc.bind(u['id'], body.cookie.strip(), b['revision'] if b else 0, u['session_token'])
        svc.revoke(u['id'])
        return result

    @app.put('/api/me/115/settings')
    async def save_path(body: SavePath, u=Depends(user)):
        svc.binding(u['id'])
        db.run('UPDATE bindings SET save_path=? WHERE user_id=?', (body.save_path, u['id']))
        return {'ok': True}

    @app.delete('/api/me/115')
    async def unbind(u=Depends(user)):
        async with svc.lock(u['id']):
            db.run("UPDATE bindings SET cookie=NULL,account_id=NULL,state='unbound',revision=revision+1,save_path='' WHERE user_id=?", (u['id'],))
            svc.revoke(u['id'])
        return {'ok': True}

    @app.post('/api/me/115/qrcode')
    async def new_qr(body: QRInput, u=Depends(user)):
        limit.check(('qr', u['id']), 4)
        if body.app not in ('os_windows', 'os_mac', 'os_linux', 'web', 'alipaymini'):
            raise HTTPException(422, '不支持的设备类型')
        for qr in svc.qrs.values():
            if qr['user_id'] == u['id'] and qr['state'] not in ('success', 'expired', 'cancelled', 'failed'):
                qr['state'] = 'cancelled'
                qr['job'].cancel()
        token, image = await svc.provider.qr_new(body.app)
        # Bind the login attempt to the authenticated session as well as the user.
        if not db.one('SELECT token FROM sessions WHERE token=? AND expires>?', (u['session_token'], time.time())):
            raise HTTPException(401)
        b = db.one('SELECT revision FROM bindings WHERE user_id=?', (u['id'],))
        qid = secrets.token_urlsafe(24)
        qr = {'id': qid, 'user_id': u['id'], 'session_token': u['session_token'], 'revision': b['revision'] if b else 0, 'token': token,
              'app': body.app, 'image': image, 'state': 'waiting', 'expires': time.time()+300, 'error': ''}
        svc.qrs[qid] = qr
        qr['job'] = svc.spawn(svc.qr_loop(qid))
        return {k: qr[k] for k in ('id', 'image', 'state', 'expires', 'error')}

    @app.get('/api/me/115/qrcode/{qid}')
    async def qr_status(qid: str, u=Depends(user)):
        qr = svc.qrs.get(qid)
        if not qr or qr['user_id'] != u['id']:
            raise HTTPException(404, '二维码会话不存在，请刷新')
        if time.time() >= qr['expires'] and qr['state'] not in ('success', 'cancelled', 'failed'):
            qr['state'] = 'expired'
            qr['job'].cancel()
        return {k: qr[k] for k in ('id', 'state', 'expires', 'error')}

    @app.delete('/api/me/115/qrcode/{qid}')
    async def cancel_qr(qid: str, u=Depends(user)):
        qr = svc.qrs.get(qid)
        if not qr or qr['user_id'] != u['id']:
            raise HTTPException(404)
        qr['state'] = 'cancelled'
        qr['job'].cancel()
        return {'ok': True}

    def task_view(row):
        return {**{k: row[k] for k in ('id', 'resource', 'title', 'state', 'percent', 'error', 'created', 'updated')},
                'files': json.loads(row['files'])}

    def own_task(tid, uid):
        row = db.one('SELECT t.* FROM tasks t JOIN bindings b ON t.user_id=b.user_id AND t.revision=b.revision '
                     'WHERE t.id=? AND t.user_id=?', (tid, uid))
        if not row:
            raise HTTPException(404, '任务不存在')
        return row

    @app.get('/api/me/tasks')
    async def tasks(u=Depends(user)):
        return [task_view(t) for t in db.all('SELECT t.* FROM tasks t JOIN bindings b ON t.user_id=b.user_id AND t.revision=b.revision '
                                            'WHERE t.user_id=? ORDER BY t.updated DESC LIMIT 1000', (u['id'],))]

    @app.get('/api/me/tasks/{tid}')
    async def task(tid: str, u=Depends(user)):
        return task_view(own_task(tid, u['id']))

    @app.post('/api/me/tasks', status_code=202)
    async def create_task(body: TaskInput, u=Depends(user)):
        limit.check(('submit', u['id']), 15)
        b = svc.binding(u['id'])
        resource = next((r for r in catalog.data()['items'] if r['code'] == body.code), None)
        if not resource or body.link_index >= len(resource['links']):
            raise HTTPException(404, '资源链接不存在')
        link = resource['links'][body.link_index]
        key, info_hash = link_identity(link)
        now, tid = time.time(), secrets.token_urlsafe(16)
        with db.connect() as c:
            c.execute('INSERT OR IGNORE INTO tasks(id,user_id,revision,resource,title,link,link_key,info_hash,state,created,updated) '
                      'VALUES(?,?,?,?,?,?,?,?,?,?,?)', (tid, u['id'], b['revision'], body.code, resource.get('title_zh') or resource.get('title') or body.code,
                                                       link, key, info_hash, 'submitting', now, now))
            row = dict(c.execute('SELECT * FROM tasks WHERE user_id=? AND revision=? AND link_key=?', (u['id'], b['revision'], key)).fetchone())
        svc.spawn(svc.process_task(row['id']))
        return task_view(row)

    @app.post('/api/me/tasks/{tid}/retry')
    async def retry(tid: str, u=Depends(user)):
        limit.check(('retry', u['id']), 6)
        async with svc.task_lock(tid):
            row = own_task(tid, u['id'])
            if row['state'] not in ('failed', 'missing', 'cancelled'):
                raise HTTPException(409, '任务仍在处理或已完成，无需重新提交')
            b = svc.binding(u['id'])
            if row['state'] == 'missing':
                for f in json.loads(row['files']):
                    try:
                        await svc.provider.direct(svc.cookie(b), f['pickcode'], 'LTX-Metadata/1.0')
                    except UpstreamError as e:
                        if e.kind != 'missing':
                            raise
                    else:
                        raise HTTPException(409, '任务仍有可用文件，请选择其他文件播放；未清理115记录')
                await svc.provider.remove_record(svc.cookie(b), row['info_hash'])
            svc.task_update(row, state='submitting', error='', files='[]', scan_page=1, percent=0,
                            created=time.time(), next_check=0)
        svc.spawn(svc.process_task(tid))
        return {'ok': True}

    @app.post('/api/me/tasks/{tid}/cancel')
    async def cancel_task(tid: str, u=Depends(user)):
        limit.check(('cancel', u['id']), 10)
        async with svc.task_lock(tid):
            row = own_task(tid, u['id'])
            if row['state'] == 'cancelled':
                return {'ok': True}
            if row['state'] not in ('submitting', 'unknown', 'downloading'):
                raise HTTPException(409, '任务已结束，无法取消；已完成任务可以删除')
            # A queued submission has not sent a write while we own this lock.
            if row['state'] != 'submitting':
                b = svc.binding(u['id'])
                await svc.provider.remove_record(svc.cookie(b), row['info_hash'])
            if not svc.current(u['id'], row['revision']):
                raise HTTPException(409, '115绑定已变化，请刷新任务')
            svc.task_update(row, state='cancelled', error='', next_check=0)
        return {'ok': True}

    @app.delete('/api/me/tasks/{tid}')
    async def delete_task(tid: str, u=Depends(user)):
        limit.check(('delete-task', u['id']), 10)
        async with svc.task_lock(tid):
            row = own_task(tid, u['id'])
            if row['state'] not in ('completed', 'missing', 'failed', 'cancelled'):
                raise HTTPException(409, '请先取消进行中的任务')
            if row['state'] in ('completed', 'missing'):
                b = svc.binding(u['id'])
                await svc.provider.remove_task_files(svc.cookie(b), row['info_hash'])
            if not svc.current(u['id'], row['revision']):
                raise HTTPException(409, '115绑定已变化，请刷新任务')
            with db.connect() as c:
                c.execute('DELETE FROM tickets WHERE task_id=? AND user_id=?', (tid, u['id']))
                c.execute('DELETE FROM tasks WHERE id=? AND user_id=?', (tid, u['id']))
            picks = {f['pickcode'] for f in json.loads(row['files'])}
            for key in list(svc.cache):
                if key[0] == u['id'] and key[2] in picks:
                    svc.cache.pop(key, None)
        return {'ok': True}

    @app.get('/api/players')
    async def players(u=Depends(user)):
        return PLAYERS

    def ticket_row(token):
        row = db.one('SELECT t.* FROM tickets t JOIN users u ON u.id=t.user_id '
                     'JOIN bindings b ON b.user_id=t.user_id WHERE t.token=? AND t.expires>? '
                     'AND u.disabled=0 AND b.revision=t.revision AND b.cookie IS NOT NULL', (digest(token), time.time()))
        if not row:
            raise HTTPException(410, '播放地址已失效，请回到站点重新准备播放')
        return row

    @app.post('/api/playback/tickets')
    async def prepare(body: TicketInput, request: Request, u=Depends(user)):
        limit.check(('prepare', u['id']), 30)
        row = own_task(body.task_id, u['id'])
        selected = next((f for f in json.loads(row['files']) if f['pickcode'] == body.pickcode), None)
        if row['state'] not in ('completed', 'missing') or not selected:
            raise HTTPException(404, '任务中没有此视频')
        b = svc.binding(u['id'])
        try:
            # Validation URL is not reused for players: their UA differs.
            await svc.provider.direct(svc.cookie(b), body.pickcode, 'LTX-Metadata/1.0')
        except UpstreamError as e:
            if e.kind == 'missing':
                svc.task_update(row, state='missing', error='文件已失效，可选择其他文件或明确重试')
            raise
        if not svc.current(u['id'], row['revision']):
            raise HTTPException(409, '账号绑定已变化，请重新准备')
        own_task(body.task_id, u['id'])  # May have been deleted during the metadata request.
        if not db.one('SELECT token FROM sessions WHERE token=? AND expires>?', (u['session_token'], time.time())):
            raise HTTPException(401)
        token, now = secrets.token_urlsafe(32), time.time()
        db.run('INSERT INTO tickets VALUES(?,?,?,?,?,?,?,?)',
               (digest(token), u['id'], row['revision'], row['id'], body.pickcode, selected['name'], now+cfg.ticket_ttl, now))
        base = cfg.public_url or str(request.base_url).rstrip('/')
        return {'url': base+'/play/'+token, 'm3u_url': base+'/play/'+token+'/playlist.m3u',
                'expires': now+cfg.ticket_ttl, 'name': selected['name']}

    @app.get('/api/playback/tickets')
    async def ticket_list(u=Depends(user)):
        return db.all('SELECT token AS id,name,created,expires FROM tickets WHERE user_id=? AND expires>? ORDER BY created DESC', (u['id'], time.time()))

    @app.delete('/api/playback/tickets/{tid}')
    async def revoke_ticket(tid: str, u=Depends(user)):
        db.run('DELETE FROM tickets WHERE token=? AND user_id=?', (tid, u['id']))
        return {'ok': True}

    @app.api_route('/play/{token}', methods=['GET', 'HEAD'])
    async def play(token: str, request: Request):
        row = ticket_row(token)
        ua = request.headers.get('user-agent', '')
        if len(ua) > 2048:
            raise HTTPException(400, 'User-Agent过长')
        target = await svc.direct(row, ua)
        ticket_row(token)  # account might have been disabled while awaiting 115
        return RedirectResponse(target, status_code=302, headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})

    @app.get('/play/{token}/playlist.m3u')
    async def playlist(token: str, request: Request):
        row = ticket_row(token)
        name = row['name'].replace('\r', ' ').replace('\n', ' ')
        base = cfg.public_url or str(request.base_url).rstrip('/')
        return Response(f'#EXTM3U\n#EXTINF:-1,{name}\n{base}/play/{token}\n', media_type='audio/x-mpegurl',
                        headers={'Content-Disposition': 'attachment; filename="playlist.m3u"'})

    if (cfg.static / 'assets').exists():
        app.mount('/assets', StaticFiles(directory=cfg.static / 'assets'), name='assets')

    @app.get('/')
    async def index():
        if not (cfg.static / 'index.html').exists():
            return JSONResponse({'detail': '前端尚未构建，请运行 npm run build'}, status_code=503)
        return FileResponse(cfg.static / 'index.html', headers={'Cache-Control': 'no-cache'})

    return app

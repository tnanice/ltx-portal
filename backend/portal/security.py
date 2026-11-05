import hashlib
import os
import secrets
import time
from collections import OrderedDict

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from cryptography.fernet import Fernet
from fastapi import HTTPException, Request

passwords = PasswordHasher()
DUMMY_HASH = passwords.hash('not-an-account-password')


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_ok(encoded, value):
    try:
        return passwords.verify(encoded, value)
    except (VerificationError, InvalidHashError):
        return False


class Vault:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / 'master.key'
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(Fernet.generate_key())
        path.chmod(0o600)
        self.fernet = Fernet(path.read_bytes().strip())

    def encrypt(self, value):
        return self.fernet.encrypt(value.encode()).decode()

    def decrypt(self, value):
        return self.fernet.decrypt(value.encode()).decode()


class Limiter:
    def __init__(self):
        self.buckets = OrderedDict()

    def check(self, key, limit=10, period=60):
        now = time.monotonic()
        values = [t for t in self.buckets.pop(key, []) if t > now - period]
        self.buckets[key] = values
        if len(self.buckets) > 10000:
            self.buckets.popitem(last=False)
        if len(values) >= limit:
            raise HTTPException(429, '操作过于频繁，请稍后再试', headers={'Retry-After': str(period)})
        values.append(now)


def user(request: Request):
    token = request.cookies.get('ltx_session', '')
    r = request.app.state.db.one(
        'SELECT u.*, s.csrf,s.token AS session_token FROM sessions s JOIN users u ON u.id=s.user_id '
        'WHERE s.token=? AND s.expires>? AND u.disabled=0', (digest(token), time.time()))
    if not r:
        raise HTTPException(401, '请先登录')
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        if not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), r['csrf']):
            raise HTTPException(403, '会话校验失败，请刷新页面')
    return r


def admin(request: Request):
    u = user(request)
    if u['role'] != 'admin':
        raise HTTPException(403, '需要管理员权限')
    return u


def public_user(u):
    return {k: u[k] for k in ('id', 'username', 'role', 'disabled')}

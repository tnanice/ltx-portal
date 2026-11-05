"""Small, short SQLite transactions. Never hold a transaction across a network await."""
import json
import sqlite3
import time
from contextlib import contextmanager

SCHEMA = '''
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
 role TEXT NOT NULL DEFAULT 'user', disabled INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
 token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), csrf TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS bindings (
 user_id INTEGER PRIMARY KEY REFERENCES users(id), revision INTEGER NOT NULL DEFAULT 0,
 account_id TEXT, cookie TEXT, state TEXT NOT NULL DEFAULT 'unbound', checked REAL, save_path TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS tasks (
 id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), revision INTEGER NOT NULL,
 resource TEXT NOT NULL, title TEXT NOT NULL, link TEXT NOT NULL, link_key TEXT NOT NULL,
 info_hash TEXT NOT NULL, state TEXT NOT NULL, percent REAL NOT NULL DEFAULT 0,
 files TEXT NOT NULL DEFAULT '[]', error TEXT NOT NULL DEFAULT '', created REAL NOT NULL,
 updated REAL NOT NULL, next_check REAL NOT NULL DEFAULT 0, scan_page INTEGER NOT NULL DEFAULT 1,
 UNIQUE(user_id, revision, link_key));
CREATE TABLE IF NOT EXISTS tickets (
 token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), revision INTEGER NOT NULL,
 task_id TEXT NOT NULL REFERENCES tasks(id), pickcode TEXT NOT NULL, name TEXT NOT NULL,
 expires REAL NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS favorites (
 user_id INTEGER NOT NULL REFERENCES users(id), code TEXT NOT NULL, PRIMARY KEY(user_id,code));
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS catalog (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS imports (fingerprint TEXT PRIMARY KEY, imported REAL NOT NULL);
CREATE INDEX IF NOT EXISTS task_owner ON tasks(user_id,revision,updated);
CREATE INDEX IF NOT EXISTS session_expiry ON sessions(expires);
PRAGMA user_version=1;
'''


class DB:
    def __init__(self, data):
        data.mkdir(parents=True, exist_ok=True)
        self.path = data / 'portal.sqlite3'
        with self.connect() as c:
            version = c.execute('PRAGMA user_version').fetchone()[0]
            if version > 1:
                raise RuntimeError('Database is newer than this application; restore a matching image.')
            c.executescript(SCHEMA)
        self.path.chmod(0o600)
        if self.setting('sync') is None:
            self.set('sync', {'enabled': True, 'interval_seconds': 3600, 'next_sync': 0,
                              'last_attempt': None, 'last_success': None, 'last_error': '',
                              'running': False, 'count': 0, 'etag': '', 'modified': ''})

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=5)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('PRAGMA journal_mode=WAL')
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def one(self, sql, args=()):
        with self.connect() as c:
            r = c.execute(sql, args).fetchone()
            return dict(r) if r else None

    def all(self, sql, args=()):
        with self.connect() as c:
            return [dict(r) for r in c.execute(sql, args)]

    def run(self, sql, args=()):
        with self.connect() as c:
            return c.execute(sql, args).lastrowid

    def setting(self, key, default=None):
        r = self.one('SELECT value FROM settings WHERE key=?', (key,))
        return json.loads(r['value']) if r else default

    def set(self, key, value):
        self.run('INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                 (key, json.dumps(value, ensure_ascii=False)))

    def cleanup(self):
        self.run('DELETE FROM sessions WHERE expires<?', (time.time(),))
        self.run('DELETE FROM tickets WHERE expires<?', (time.time(),))

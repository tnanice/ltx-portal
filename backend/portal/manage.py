"""Explicit, idempotent legacy import and consistent online SQLite backups."""
import argparse
import asyncio
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path

from .catalog import validate_catalog
from .config import Config
from .db import DB
from .provider import Provider, link_identity
from .security import Vault


async def import_legacy(args, config):
    db = DB(config.data)
    owner = db.one("SELECT id FROM users WHERE username=? AND role='admin' AND disabled=0", (args.admin,))
    if not owner:
        raise ValueError('指定管理员不存在；请先启动一次新站点完成初始化')
    raw_catalog = Path(args.catalog).read_bytes() if args.catalog else None
    raw_cookie = Path(args.cookie).read_bytes() if args.cookie else None
    raw_tasks = Path(args.tasks).read_bytes() if args.tasks else None
    fingerprint = hashlib.sha256(str(owner['id']).encode() + (raw_catalog or b'') + (raw_cookie or b'') + (raw_tasks or b'')).hexdigest()
    if db.one('SELECT fingerprint FROM imports WHERE fingerprint=?', (fingerprint,)):
        print('相同数据已导入，本次跳过。')
        return
    payload = validate_catalog(json.loads(raw_catalog)) if raw_catalog else None
    tasks = json.loads(raw_tasks) if raw_tasks else {}
    if not isinstance(tasks, dict):
        raise ValueError('旧任务文件必须是对象')
    if tasks and not raw_cookie:
        raise ValueError('导入旧任务时必须同时提供对应Cookie，以建立明确的用户归属')
    index = {}
    if payload:
        for item in payload['items']:
            for link in item['links']:
                try:
                    key, h = link_identity(link)
                    index[h] = (item['code'], item.get('title_zh') or item.get('title') or item['code'], link, key)
                except Exception:
                    pass
    prepared = []
    for info_hash, task in tasks.items():
        if not isinstance(task, dict) or not task.get('pick_code'):
            continue
        record = index.get(info_hash.lower())
        if not record and task.get('url'):
            try:
                key, h = link_identity(task['url'])
                record = (str(task.get('name') or '旧任务'), str(task.get('name') or '旧任务'), task['url'], key)
            except Exception:
                continue
        if record:
            prepared.append((info_hash.lower(), task, record))
    print(f"检查结果：资源 {len(payload['items']) if payload else 0} 条，可归属的完成任务 {len(prepared)} 条。")
    if args.dry_run:
        print('只检查格式；未验证Cookie、未修改数据。')
        return
    account, encrypted = None, None
    if raw_cookie:
        existing = db.one('SELECT cookie FROM bindings WHERE user_id=?', (owner['id'],))
        if existing and existing['cookie']:
            raise ValueError('管理员已绑定115。为避免覆盖，请先在站点明确解绑后导入。')
        cookie = raw_cookie.decode().strip()
        account = await Provider().verify(cookie)
        encrypted = Vault(config.data/'secrets').encrypt(cookie)
    now = time.time()
    with db.connect() as c:
        if c.execute('SELECT fingerprint FROM imports WHERE fingerprint=?', (fingerprint,)).fetchone():
            return
        if payload:
            c.execute('INSERT INTO catalog VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (json.dumps(payload, ensure_ascii=False),))
            s = json.loads(c.execute("SELECT value FROM settings WHERE key='sync'").fetchone()[0])
            s.update(count=len(payload['items']), etag='', modified='', next_sync=0)
            c.execute("UPDATE settings SET value=? WHERE key='sync'", (json.dumps(s),))
        if encrypted:
            old = c.execute('SELECT revision,cookie FROM bindings WHERE user_id=?', (owner['id'],)).fetchone()
            if old and old['cookie']:
                raise ValueError('导入期间绑定发生变化，未写入数据')
            revision = (old['revision'] if old else 0)+1
            c.execute('INSERT INTO bindings(user_id,revision,account_id,cookie,state,checked) VALUES(?,?,?,?,?,?) '
                      'ON CONFLICT(user_id) DO UPDATE SET revision=excluded.revision,account_id=excluded.account_id,cookie=excluded.cookie,state=excluded.state,checked=excluded.checked',
                      (owner['id'], revision, account, encrypted, 'ok', now))
            c.execute('DELETE FROM tickets WHERE user_id=?', (owner['id'],))
            for h, task, (code, title, link, key) in prepared:
                files = [{'pickcode': task['pick_code'], 'name': task.get('name') or title, 'size': task.get('size') or 0}]
                c.execute('INSERT OR IGNORE INTO tasks(id,user_id,revision,resource,title,link,link_key,info_hash,state,files,percent,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                          (secrets.token_urlsafe(16), owner['id'], revision, code, title, link, key, h, 'completed', json.dumps(files, ensure_ascii=False), 100,
                           task.get('added_at') or now, task.get('done_at') or now))
        c.execute('INSERT INTO imports VALUES(?,?)', (fingerprint, now))
    print('导入完成。旧文件未修改；导入的凭据与任务仅属于指定管理员。')


def backup(args, config):
    if not (config.data/'portal.sqlite3').exists():
        raise ValueError('数据库不存在')
    target = Path(args.output)
    if target.exists():
        raise ValueError('输出文件已存在，请使用新的备份文件名')
    with tempfile.TemporaryDirectory() as folder:
        staging = Path(folder)
        source = sqlite3.connect(f'file:{config.data / "portal.sqlite3"}?mode=ro', uri=True)
        destination = sqlite3.connect(staging/'portal.sqlite3')
        try:
            source.backup(destination)
        finally:
            destination.close(); source.close()
        shutil.copytree(config.data/'secrets', staging/'secrets')
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'wb') as output:
            with tarfile.open(fileobj=output, mode='w:gz') as archive:
                archive.add(staging/'portal.sqlite3', arcname='portal.sqlite3')
                archive.add(staging/'secrets', arcname='secrets')
    print('一致性备份完成。备份包含账号凭据和加密密钥，请妥善保存。')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    imp = sub.add_parser('import-legacy')
    imp.add_argument('--admin', required=True)
    imp.add_argument('--catalog'); imp.add_argument('--cookie'); imp.add_argument('--tasks')
    imp.add_argument('--dry-run', action='store_true')
    bak = sub.add_parser('backup'); bak.add_argument('--output', required=True)
    args = p.parse_args()
    try:
        if args.command == 'import-legacy':
            asyncio.run(import_legacy(args, Config()))
        else:
            backup(args, Config())
    except Exception as e:
        # No upstream exception reprs: may contain signed URLs or cookies.
        from .provider import UpstreamError
        print(e.message if isinstance(e, UpstreamError) else str(e) if isinstance(e, ValueError) else '操作失败，请检查输入文件、权限和115连接状态。')
        raise SystemExit(1)


if __name__ == '__main__':
    main()

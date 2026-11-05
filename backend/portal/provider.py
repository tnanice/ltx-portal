"""115 boundary: native asynchronous requests, no abandoned timeout threads."""
import asyncio
import base64
import binascii
import re
from urllib.parse import parse_qs, unquote, urlsplit

from p115client import P115Client, check_response


class UpstreamError(Exception):
    def __init__(self, kind, message, code=None):
        super().__init__(message)
        self.kind, self.message, self.code = kind, message, code


def checked(r):
    if not isinstance(r, dict):
        raise UpstreamError('temporary', '115返回格式异常')
    if r.get('state') in (False, 0):
        code = r.get('errno', r.get('errcode', r.get('code')))
        message = str(r.get('error_msg') or r.get('message') or r.get('error') or '')
        if str(code) in ('99', '401') or '重新登录' in message:
            raise UpstreamError('invalid', '115登录已失效，请重新绑定', code)
        if '繁忙' in message or str(code) in ('911', '429'):
            raise UpstreamError('temporary', '115繁忙或要求验证，请稍后重试', code)
        raise UpstreamError('rejected', f'115拒绝操作（错误码 {code or "未知"}）', code)
    return r


def cookie_from_qr(r):
    checked(r)
    data = r.get('data') or {}
    cookie = data.get('cookie') if isinstance(data, dict) else None
    if not isinstance(cookie, dict) or not all(cookie.get(k) for k in ('UID', 'CID', 'SEID')):
        raise UpstreamError('rejected', '扫码结果没有完整登录凭据，请重新扫码')
    return '; '.join(f'{k}={cookie[k]}' for k in ('UID', 'CID', 'SEID', 'KID') if cookie.get(k))


def link_identity(link):
    m = re.fullmatch(r'ed2k://\|file\|[^|]+\|\d+\|([0-9a-fA-F]{32})\|(?:[^\r\n]*)', link)
    if m:
        h = m[1].lower()
        return 'ed2k:' + h, h
    if link.lower().startswith('magnet:?'):
        for xt in parse_qs(urlsplit(link).query).get('xt', []):
            if not xt.lower().startswith('urn:btih:'):
                continue
            h = xt[9:]
            if re.fullmatch('[0-9a-fA-F]{40}', h):
                return 'btih:' + h.lower(), h.lower()
            if re.fullmatch('[a-zA-Z2-7]{32}', h):
                try:
                    h = base64.b32decode(h.upper()).hex()
                    return 'btih:' + h, h
                except binascii.Error:
                    pass
    raise UpstreamError('rejected', '仅支持完整的 ed2k 或 BTIH 磁力链接')


class Provider:
    def __init__(self):
        self.global_limit = asyncio.Semaphore(8)

    async def call(self, fn, *args, timeout=12, **kwargs):
        try:
            async with self.global_limit:
                async with asyncio.timeout(timeout):
                    return await fn(*args, async_=True, timeout=min(timeout, 8), retries=0, **kwargs)
        except UpstreamError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Never expose SDK exception strings: they may contain cookies or signed URLs.
            name = type(e).__name__
            if 'Authentication' in name or 'Login' in name:
                raise UpstreamError('invalid', '115登录失效，请重新绑定') from None
            if isinstance(e, IsADirectoryError):
                raise UpstreamError('directory', '这是目录，请选择其中的视频') from None
            if isinstance(e, FileNotFoundError):
                raise UpstreamError('missing', '115文件不存在或已失效') from None
            raise UpstreamError('temporary', '115暂时不可达，请稍后重试') from None

    def client(self, cookie):
        return P115Client(cookie)

    async def verify(self, cookie):
        pairs = dict(part.strip().split('=', 1) for part in cookie.split(';') if '=' in part)
        if not all(pairs.get(k) for k in ('UID', 'CID', 'SEID')):
            raise UpstreamError('invalid', 'Cookie缺少 UID、CID 或 SEID')
        c = self.client(cookie)
        valid = await self.call(c.login_status)
        if not valid:
            raise UpstreamError('invalid', '115未通过登录验证，原绑定未更改')
        account = str(c.user_id)
        if not account.isdigit():
            raise UpstreamError('invalid', '无法验证115账号身份')
        return account

    async def qr_new(self, app):
        raw = checked(await self.call(P115Client.login_qrcode_token, app=app))
        token = raw.get('data') or {}
        if not all(token.get(k) for k in ('uid', 'time', 'sign')):
            raise UpstreamError('temporary', '二维码接口返回不完整')
        png = await self.call(P115Client.login_qrcode, token['uid'], app=app)
        if not isinstance(png, bytes) or not png.startswith(b'\x89PNG'):
            raise UpstreamError('temporary', '二维码图片异常')
        return token, 'data:image/png;base64,' + base64.b64encode(png).decode()

    async def qr_poll(self, token):
        # This endpoint intentionally long-polls. A timeout is NOT an expired QR.
        try:
            async with self.global_limit:
                async with asyncio.timeout(40):
                    r = await P115Client.login_qrcode_scan_status(
                        {k: token[k] for k in ('uid', 'time', 'sign')}, async_=True, timeout=36, retries=0)
            checked(r)
            return int((r.get('data') or {}).get('status', 0))
        except (TimeoutError, asyncio.TimeoutError):
            return 0
        except UpstreamError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            raise UpstreamError('temporary', '二维码状态暂时不可达，正在重试') from None

    async def qr_result(self, token, app):
        return cookie_from_qr(await self.call(P115Client.login_qrcode_scan_result, token['uid'], app=app))

    async def submit(self, cookie, link, save_path=''):
        c = self.client(cookie)
        payload = {'url': link}
        if save_path:
            payload['wp_path_id'] = save_path
        # A write is never retried automatically: timeout has an unknown result.
        r = await self.call(c.clouddownload_task_add_url, payload)
        if isinstance(r, dict) and str(r.get('errcode', r.get('errno'))) == '10008':
            return {'duplicate': True, 'info_hash': link_identity(link)[1]}
        checked(r)
        data = r.get('data') or {}
        return {'duplicate': False, 'info_hash': str(data.get('info_hash') or link_identity(link)[1]).lower(), 'task': data}

    async def task_page(self, cookie, page):
        r = checked(await self.call(self.client(cookie).clouddownload_task_list, page))
        return r.get('tasks') or [], max(1, int(r.get('page_count') or 1))

    async def files(self, cookie, task):
        """Enumerate only the completed task's result directory, never the entire drive."""
        c = self.client(cookie)
        pick = task.get('pick_code') or task.get('pickcode')
        root = task.get('file_id') or task.get('fileid') or task.get('folder_id')
        is_directory = False
        if root and str(root) != '0':
            metadata = checked(await self.call(c.fs_file, str(root)))
            rows = metadata.get('data') or []
            if isinstance(rows, list) and rows:
                metadata_pick = rows[0].get('pc') or rows[0].get('pick_code')
                # file_id may be the shared destination directory while pick_code
                # identifies this task's file. Do not enumerate unrelated siblings.
                is_directory = str(rows[0].get('fc', '1')) == '0' and (not pick or metadata_pick == pick)
        # Even a pickcode can identify a directory; strict metadata lookup determines that.
        if pick and not is_directory:
            try:
                u = await self.call(c.download_url, pick, user_agent='LTX-Metadata/1.0', app='android')
                return [{'pickcode': pick, 'name': getattr(u, 'name', None) or task.get('name') or '视频',
                         'size': getattr(u, 'size', None) or task.get('size', 0)}]
            except UpstreamError as e:
                if e.kind != 'directory':
                    raise
        if not root or str(root) == '0':
            raise UpstreamError('temporary', '115尚未返回任务文件目录，稍后重新检查')
        queue, seen, result, pages = [str(root)], set(), [], 0
        while queue:
            cid = queue.pop(0)
            if cid in seen:
                continue
            seen.add(cid)
            offset = 0
            while True:
                pages += 1
                if pages > 100:
                    raise UpstreamError('rejected', '任务文件过多，暂不支持超过100页的任务目录')
                r = checked(await self.call(c.fs_files_app, {'cid': cid, 'offset': offset, 'limit': 200}, app='android'))
                entries = r.get('data') or []
                if not isinstance(entries, list):
                    raise UpstreamError('temporary', '115目录格式异常')
                for f in entries:
                    name = f.get('n') or f.get('fn') or f.get('file_name') or ''
                    fid = f.get('fid') or f.get('file_id')
                    directory = not fid or str(f.get('fc', '1')) == '0'
                    if directory:
                        child = f.get('cid') or fid
                        if child:
                            queue.append(str(child))
                    elif re.search(r'\.(mp4|mkv|avi|mov|m4v|wmv|ts|m2ts|webm|flv|mpg|mpeg)$', name, re.I):
                        pc = f.get('pc') or f.get('pick_code') or f.get('pickcode')
                        if pc:
                            result.append({'pickcode': pc, 'name': name, 'size': f.get('s', f.get('fs', 0))})
                offset += len(entries)
                if not entries or offset >= int(r.get('count', offset)):
                    break
        if not result:
            raise UpstreamError('rejected', '任务中未找到支持的视频文件')
        return result

    async def direct(self, cookie, pick, ua):
        url = await self.call(self.client(cookie).download_url, pick, user_agent=ua, app='android')
        target = str(url)
        parsed = urlsplit(target)
        flags = parse_qs(parsed.query)
        headers = getattr(url, 'headers', {}) or {}
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            raise UpstreamError('temporary', '115返回了无效的播放地址')
        if flags.get('f', [''])[0] not in ('', '0', '1') or any(k.lower() == 'cookie' and v for k, v in headers.items()):
            raise UpstreamError('unsupported', '此直链要求额外凭据，无法纯302播放；不会启用中转')
        try:
            expires = float(flags.get('t', ['0'])[0])
        except ValueError:
            expires = 0
        return target, expires

    async def remove_record(self, cookie, info_hash):
        return checked(await self.call(self.client(cookie).clouddownload_task_del,
                                       {'hash[0]': info_hash, 'flag': 0}))

    async def remove_task_files(self, cookie, info_hash):
        # Only this offline task's source files, never a bulk drive deletion.
        return checked(await self.call(self.client(cookie).clouddownload_task_del,
                                       {'hash[0]': info_hash, 'flag': 1}))

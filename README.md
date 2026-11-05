# LTX Portal · 私人镜像与115直连播放

一个可整体迁移的 Docker 项目：资源镜像、账号登录、每人独立115、自动同步、个人任务库、外部播放器。**视频仅302跳转到115，服务端没有视频代理或转码接口。**

仓库和镜像不含资源文件、数据库或115凭据：资源目录在首次启动时从 `CATALOG_URL` 拉取，115 由每个使用者自己扫码绑定。

## 快速启动

需要 Docker 与 Compose（`docker compose` 或独立版 `docker-compose` 均可）。

```sh
git clone https://github.com/tnanice/ltx-portal.git
cd ltx-portal
cp .env.example .env
# 编辑 .env：设置至少12位的 ADMIN_PASSWORD
docker compose up -d
```

默认拉取 GHCR 预构建镜像（amd64/arm64）。打开 `http://服务器IP:8896`，以 `.env` 中的管理员账号登录。8896 被占用时改 `APP_PORT`。首次启动会自动拉取目录，页面几秒内出现资源。

需要本机构建（不依赖 GHCR）时：

```sh
docker compose -f compose.yaml -f compose.build.yaml up -d --build
```

1. 在“管理”中同步资源、创建成员账号；不开放公开注册。
2. 每个人登录自己的站点账号，在“我的115”选择设备、生成二维码、使用115 App扫码确认。
3. 扫码失败可输入Cookie；必须验证成功后才会替换已有绑定。
4. 资源卡片“115离线播放”将资源提交到自己的115；“我的任务”显示进度。
5. 完成后，卡片和任务的播放行显示 PotPlayer、IINA、VLC、VidHub 图标。可见作品提前准备播放地址；图标亮起后，点击哪个就直接请求拉起哪个。“播放”使用上次选择，“…”提供复制地址和M3U。多文件任务优先匹配作品编号、排除样片，再按文件大小选择，无需逐次选文件。
6. 主页“已离线”只显示本人当前115绑定下已完成且有视频的资源卡片。卡片保留独立“复制 ed2k”按钮，多个链接可在详情中逐条复制。
7. 进行中的任务可“取消离线”，保留已生成文件。已完成任务的“删除任务和文件”会请求115删除该离线任务的对应文件，成功后删除本站记录并撤销该任务播放凭证。失败、超时不会显示成功，也不会清除本站记录。失败或已取消任务可只删除本站记录。

离线提交后立即检查完成状态，新任务短间隔检查；页面有进行中任务时每秒刷新。115本身仍在99%或没有返回文件时会继续显示进行中，不把“已提交”当作“已完成”。

首次密码只用于空数据库初始化。已有数据库时修改 `.env` 不会重置密码；请在界面中修改密码或由管理员重置。初始化后可以清空 `.env` 中的密码，账号仍保留。

## 播放器

| 平台 | 方式 |
|---|---|
| Windows PotPlayer | 点击图标时同步复制并立即通过 `/clipboard` 拉起，避免Chromium的嵌套URL解析问题；复制被拒绝时提供手动备用操作 |
| macOS IINA | `iina://weblink`，按播放器实际UA获取115直链 |
| iOS VLC | `vlc-x-callback://x-callback-url/stream` |
| Android VLC | 带 `org.videolan.vlc` 包名的 Intent |
| VidHub | `open-vidhub://x-callback-url/play`，支持iOS/Android，旧版App可能需升级 |
| 备用 | 复制播放地址、下载M3U，再手动粘贴或打开 |

浏览器必须允许外部应用拉起，播放器必须已安装。内置微信等浏览器可能阻止跳转，请用系统浏览器。PotPlayer剪贴板复制失败时提供可手动复制的地址，不回退到损坏的URL。

本站播放地址有效期12小时，只允许播放指定用户当前115绑定中的单个文件。播放器不需要浏览器Cookie。115直链按真实UA获取并短时缓存；不会把后台检查使用的UA直链交给播放器。需要额外Cookie等无法纯302播放的链接会明确报错。没有中转降级。

长时间播放中115直链过期时，播放器若重新请求本站地址可获得新直链；若播放器只重试旧CDN地址，需要回到站点重新打开。本站看不到播放器直连CDN后的播放进度，也不能撤销已交付的115直链。

## 同步与源站状态

管理页可修改同步间隔（5分钟～7天）、暂停自动同步、立即同步。修改不需要重启。任务持久化在SQLite；请求失败、结构变化、空响应、突然返回空目录等均保留最后有效快照。

源站每5分钟检查一次目录接口，明确标注检查时间；“源站可达”表示最近检查结果，不是实时保证。源站的新增数据不依赖页面HTML，所以源站样式更新不会影响镜像；JSON接口结构变化会显示兼容性错误。

## 公网与反向代理

已有反向代理时在 `.env` 设置：

```dotenv
PUBLIC_URL=https://ltx.example.com
COOKIE_SECURE=true
ALLOWED_HOSTS=ltx.example.com,127.0.0.1,localhost
BIND_IP=127.0.0.1
```

代理到本机 `APP_PORT`，保留 Host。本站不信任任意 `X-Forwarded-*` 请求头，使用显式 `PUBLIC_URL` 生成播放地址。若代理运行于另一台主机，按实际网络设置监听地址和防火墙。

需要附带自动HTTPS时，再设置 `DOMAIN=ltx.example.com`，DNS指向服务器：

```sh
docker compose -f compose.yaml -f compose.tls.yaml up -d
```

此模式也应设置 `BIND_IP=127.0.0.1`，只公开80/443。Caddy持久化目录也在 `data/` 内。公网必须使用可从播放器所在设备访问的域名。HTTPS方式测试需要真实域名，不会自动替你改DNS。

## 升级

先做一次一致性备份（见下文），然后把 `.env` 中的 `LTX_TAG` 改为新版本号：

```sh
docker compose pull
docker compose up -d
```

## 整目录迁移

安装了 Docker 和 Compose 的目标机器，无需安装Node/Python。预构建镜像支持 amd64 与 arm64。

1. 原机器 `docker compose stop`，停止写入。
2. 完整复制本目录（特别是隐藏文件 `.env` 和整个 `data/`）。可排除 `frontend/node_modules`、`frontend/dist` 和测试缓存。
3. 目标机器修改端口、域名配置，运行 `docker compose up -d`。
4. 确认登录、资源、任务和115绑定正常后再停用旧入口。

`data/secrets/master.key` 是解密115凭据必需的密钥，不能遗漏。数据库存在有效凭据但密钥丢失时，应用拒绝启动，防止误生成新密钥。SQLite数据目录应在本机文件系统上，不使用NFS/SMB共享数据库文件。搬迁后115可能要求重新登录，这与源站/本站账号无关。

同架构且目标无法联网拉取镜像时，先把镜像一起搬走：

```sh
docker save ghcr.io/tnanice/ltx-portal:1.0.0 -o ltx-portal-image.tar
# 目标机器
docker load -i ltx-portal-image.tar
docker compose up -d
```

预构建镜像已覆盖 amd64/arm64，目标机器直接 `docker pull` 即可，无需在对应架构重新构建。可选Caddy镜像需要另外拉取或导出。

## 一致性备份

运行中可使用SQLite备份API，避免直接复制正在写入的数据库：

```sh
docker compose exec --user portal app python -m portal.manage backup --output /data/backup-2026-11-04.tar.gz
```

备份包含数据库和加密密钥，权限0600；请另存部署文件和 `.env`。恢复时先停止应用，在**空的** `data/` 中解压可信备份，再启动。不要在服务运行时覆盖数据库。也可以停机后直接备份整个目录。

## 从旧项目导入

导入只写入指定管理员，不能导入到普通成员；相同输入重复执行会跳过。不会修改旧文件，不会提交/删除115离线任务。已有115绑定时拒绝覆盖，需先由用户明确解绑。只有匹配到有效资源链接的已完成任务会导入。

将旧文件复制到 `data/import/`，保持私密权限：`items.json`、`115-cookies.txt`、`tasks.json`。先检查：

```sh
docker compose exec --user portal app python -m portal.manage import-legacy \
  --admin admin --catalog /data/import/items.json \
  --cookie /data/import/115-cookies.txt --tasks /data/import/tasks.json --dry-run
```

移除 `--dry-run` 后正式导入；导入前会验证Cookie。完成后安全移走或删除 `data/import/` 中的明文凭据副本。只导入目录快照时仅传 `--catalog` 即可。命令不会打印Cookie、二维码令牌或直链。

## 开发与验证

```sh
# Python 3.12+
python3.12 -m venv .venv
.venv/bin/pip install -r backend/requirements.lock
cd backend
PYTHONPATH=. ../.venv/bin/pytest -q
# 前端，Node 22.12+
cd ../frontend
npm ci
npm test
npm run build
```

运行后端时指定 `DATA_DIR`、`ADMIN_PASSWORD`、`STATIC_DIR`，执行 `uvicorn portal.main:create_app --factory --workers 1 --port 8000 --no-access-log --no-proxy-headers`。开发前端 `npm run dev` 将API代理到8000。必须保持一个API worker，以确保后台调度唯一；不使用 `--reload` 运行生产服务。

所有Python传递依赖锁定于 `backend/requirements.lock`，前端依赖锁定于 `package-lock.json`。应用访问日志默认关闭以避免记录带凭证的播放URL；结构化调度错误不含账号密钥。

播放器适配器位于 `frontend/src/players.ts`，新增播放器只需注册平台、名称及启动URL构造器，并更新后端 `/api/players` 能力列表。不得添加执行任意脚本的用户配置。

## 接口约定

登录返回CSRF令牌；浏览器发送HttpOnly会话Cookie，所有写操作加 `X-CSRF-Token`。无开放注册、无跨源CORS、无共享管理Token。

| 接口组 | 权限 |
|---|---|
| `/api/auth/*` | 登录/会话/改密/退出 |
| `/api/admin/*` | 管理员：用户管理、同步设置 |
| `/api/catalog/*` | 已登录用户：快照与检查状态 |
| `/api/me/115/*` | 本人绑定、扫码、验证、保存位置 |
| `/api/me/tasks/*` | 本人当前绑定的任务与明确重试 |
| `/api/me/favorites/*` | 本人收藏 |
| `/api/playback/tickets` | 创建、列出、撤销本人的播放凭证 |
| `/api/players` | 已登录用户：播放器能力 |
| `GET/HEAD /play/{ticket}` | 单文件凭证：仅返回302或错误 |
| `/healthz` | 仅本服务存活，不探测115、不返回账号信息 |

## 验收边界

自动测试可验证权限、状态机、同步回退、URL生成、返回302和目录迁移。真人扫码确认以及PotPlayer/IINA/VLC/VidHub实际启动、解码、拖动和重复播放必须在对应设备上实测。不能以二维码生成或协议链接生成代替完整验收。具体本次测试结果见 `VALIDATION.md`。

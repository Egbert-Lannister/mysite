# Upgrade Roadmap

> 本文档记录站点未来的升级路径与可选改进，按优先级排序。
> 每条目标都给出 **动机 / 方案 / 实施步骤 / 验收条件**，方便日后逐项推进。

---

## 1. 异地备份（高优先级 · 防数据丢失）

**动机**
- 当前 `db.sqlite3` 虽然有每日本地备份（`/root/mysite/backups/`，cron 每天 03:00 跑），
  但所有备份仍在同一台机器、同一块磁盘上。一旦服务器失联 / 磁盘损坏 / 整个实例被销毁，
  本地备份会一起消失。
- 5月7日的事故已经证明：单点存储是不够的。

**方案 A：rsync 到另一台机器（最简单）**

```bash
# /root/mysite/scripts/backup_offsite.sh
#!/usr/bin/env bash
set -euo pipefail
LATEST=$(ls -1t /root/mysite/backups/db.sqlite3.* | head -n1)
rsync -avz --delete \
  /root/mysite/backups/ \
  user@backup-host:/srv/blog-backups/
```

cron：
```cron
30 3 * * * /root/mysite/scripts/backup_offsite.sh >> /root/mysite/backups/offsite.log 2>&1
```

**方案 B：推到对象存储（推荐，最稳）**

任选其一：
- **Cloudflare R2**（免费 10GB / 月 1M 请求）
- **AWS S3 Glacier Deep Archive**（约 $0.00099/GB/月）
- **Backblaze B2**（免费 10GB）

```bash
# /root/mysite/scripts/backup_s3.sh
#!/usr/bin/env bash
set -euo pipefail
TS=$(date +%Y%m%d_%H%M%S)
LATEST=$(ls -1t /root/mysite/backups/db.sqlite3.* | head -n1)
aws s3 cp "$LATEST" "s3://mysite-backups/db.sqlite3.$TS" \
  --storage-class STANDARD_IA
# 异地侧也保留 30 天
aws s3 ls s3://mysite-backups/ \
  | awk '{print $4}' \
  | sort -r | tail -n +31 \
  | xargs -I{} aws s3 rm "s3://mysite-backups/{}"
```

**实施步骤**
1. 注册对象存储账号，创建一个 Bucket（例如 `mysite-backups`）
2. 在服务器上配置 `aws-cli` 或 `rclone` 凭证
3. 写脚本（参见上面），加 cron 到 `0 4 * * *`（错开本地备份时间）
4. **首次手动跑一次** 确认推送成功
5. 用一台干净机器尝试 `aws s3 cp` 拉回备份再 `sqlite3 db.sqlite3 ".tables"` 验证可读

**验收条件**
- [ ] 本地备份完成后能自动推送到异地
- [ ] 至少能成功从异地拉回一次完整 db 并打开
- [ ] cron 日志连续 7 天无错误

---

## 2. SQLite → 托管 Postgres（中长期 · 数据可靠性）

**动机**
- SQLite 在单文件数据库的场景下性能足够，但**没有 Point-In-Time Recovery (PITR)**：
  你只能恢复到最近一次完整备份的时间点，期间的写入仍会丢失。
- 托管 Postgres 内置 WAL 流复制 + 7~30 天的 PITR，**可以恢复到任意一秒**。
- 我们的代码已经写过 SQLite/PG 双兼容（`posts/views.py` 的 `search()` 用 `connection.vendor` 区分），
  Postgres 还能解锁更强的 `SearchVector` 全文搜索。

**候选托管商**

| 服务 | 免费档 | PITR | 备注 |
|------|---------|------|------|
| **Supabase** | 500 MB · 7 天 PITR | ✅ | UI 友好，自带 Auth/Realtime（暂不需要）|
| **Neon** | 0.5 GB · 7 天 PITR | ✅ | Serverless，闲置自动 scale-to-zero |
| **Render Postgres** | 1 GB · 1 天保留 | ✅ | 与 Render 部署集成好 |
| **Railway** | 0.5 GB · 1 天 PITR | ⚠️ 短 | 简单 |

推荐 **Neon**（无冷启动收费 + serverless）或 **Supabase**（社区大、文档全）。

**实施步骤**

1. 创建 Postgres 实例，记下 `DATABASE_URL`
2. 在服务器 `.env` 加上：
   ```env
   DATABASE_URL=postgresql://user:pass@host:port/dbname?sslmode=require
   ```
   `mysite/settings.py` 已经用 `dj_database_url.parse(os.environ.get('DATABASE_URL', ...))`，
   设置后会自动切换。
3. 数据迁移：
   ```bash
   # 1. 在原 SQLite 上 dumpdata
   python manage.py dumpdata --natural-foreign --natural-primary \
     --exclude=contenttypes --exclude=auth.permission \
     --indent=2 > /tmp/full_dump.json

   # 2. 临时切到 Postgres 跑迁移
   export DATABASE_URL='postgresql://...'
   python manage.py migrate

   # 3. 导入数据
   python manage.py loaddata /tmp/full_dump.json
   ```
4. 验证文章数 / 用户数 / Series 数与原库一致
5. 重启 gunicorn，访问首页/详情页/admin 全链路冒烟
6. **保留 SQLite 文件作为只读副本一周**（双写或仅观察都行）
7. 一周稳定后，可以从 `requirements.txt` 移除对 SQLite 的依赖（实际上 stdlib 自带，无需移除）

**验收条件**
- [ ] `connection.vendor == 'postgresql'`，`search()` 走 `SearchVector` 路径正常
- [ ] 文章详情页能正常渲染（Postgres GIN 索引在 `posts.0001_initial` 之后会被 0007 之外的迁移覆盖，需重新 `migrate`）
- [ ] 在托管控制台手动触发一次 PITR 恢复演练，确认能拉到任意时间点
- [ ] 旧 SQLite 文件归档到 `backups/legacy_sqlite_*.sqlite3`

**注意事项**
- 切到 Postgres 后，不要再用 `sqlite3 db.sqlite3` 直接编辑数据
- 本地开发可继续用 SQLite（不设 `DATABASE_URL` 即可）
- `posts/migrations/0001_initial` 里有针对 PG 的 `GinIndex`，迁移时确保 `psycopg2-binary` 已装

---

## 3. 其他可考虑的改进（低优先级）

### 3.1 错误监控（Sentry / GlitchTip）
**动机**：5月7日的 500 错误肉眼发现 = 已经挂了一段时间。需要主动告警。

```python
# settings.py
import sentry_sdk
sentry_sdk.init(
    dsn=os.environ.get('SENTRY_DSN', ''),
    traces_sample_rate=0.0,
)
```

### 3.2 日志改进
当前 `gunicorn-error.log` 不会捕获到 Django 视图层抛出的异常 traceback。
加一段简单的日志配置，让 Django 把 ERROR 日志写到独立文件：

```python
# settings.py
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': BASE_DIR / 'logs' / 'django.log',
            'maxBytes': 10 * 1024 * 1024,
            'backupCount': 5,
        },
    },
    'loggers': {
        'django.request': {
            'handlers': ['file'],
            'level': 'ERROR',
            'propagate': False,
        },
    },
}
```

### 3.3 静态文件 CDN
媒体目录 `media/posts/<slug>/*.png` 现在由 WhiteNoise + gunicorn 直接服务，
访问大量图片时会占用 worker。可以前置：
- Cloudflare 免费版（最简单，DNS 改 proxied 即可）
- 或 R2 + Custom Domain（彻底脱离主机带宽）

### 3.4 Health check 端点
加一个 `GET /healthz` 视图返回 `200 OK`，让运行环境（K8s probe / UptimeRobot）能持续探活。

```python
# posts/views.py
from django.http import JsonResponse
def healthz(request):
    return JsonResponse({"ok": True})
```

```python
# urls.py
path('healthz', posts_views.healthz),
```

### 3.5 升级到容器化部署
当前 gunicorn 是裸进程，每次重启都得手动 `kill + nohup`。可以打包成 Docker：
- 一份 `Dockerfile` 锁定 Python / 系统库版本
- `docker compose up -d` 一键起所有服务（含 nginx 反代）
- 真出问题时一句 `docker restart` 就能恢复

---

## 完成情况追踪

- [x] 本地每日备份脚本（`scripts/backup_db.sh`）+ cron `0 3 * * *`
- [x] 备份保留 14 天 + 0 字节防御
- [ ] **异地备份**（rsync 或 S3）
- [ ] **托管 Postgres + PITR**
- [ ] Sentry / GlitchTip
- [ ] Django 日志配置
- [ ] 静态/媒体 CDN
- [ ] `/healthz` 端点
- [ ] Docker 化部署

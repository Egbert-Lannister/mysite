# Egbert 技术博客

一个现代化的 Django 技术博客，支持 Markdown 写作、暗黑模式和精美 UI。

## 功能特性

- **Markdown + YAML 元数据**：使用 Markdown 编写文章，支持 YAML front matter
- **ZIP 包上传**：支持上传包含图片的 ZIP 压缩包
- **内容去重**：通过 SHA256 哈希自动检测重复内容
- **暗黑/亮色模式**：跟随系统偏好，支持手动切换
- **Giscus 评论**：基于 GitHub Discussions 的评论系统
- **代码高亮**：语法高亮 + 一键复制代码
- **自动目录**：长文章自动生成目录导航
- **社交分享**：复制链接、微信二维码、Twitter/X 分享
- **RSS 订阅**：完整的 RSS 支持
- **全文搜索**：PostgreSQL 全文搜索（支持 SQLite 降级）
- **现代后台**：Django Unfold 管理界面

## 技术栈

| 组件 | 技术 |
|------|------|
| 后端 | Django 5.2 |
| 前端 | Tailwind CSS (CDN) |
| 数据库 | PostgreSQL / SQLite |
| 后台 UI | django-unfold |
| 标签 | django-taggit |
| 静态文件 | WhiteNoise |
| 服务器 | Gunicorn + Nginx |
| SSL | Let's Encrypt |

## 快速开始

### 环境要求

- Python 3.11+
- PostgreSQL（可选，开发环境可用 SQLite）

### 安装步骤

```bash
# 克隆仓库
git clone https://github.com/Egbert-Lannister/mysite.git
cd mysite

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 运行数据库迁移
python manage.py migrate

# 创建管理员账号
python manage.py createsuperuser

# 启动开发服务器
python manage.py runserver
```

### 生产部署

```bash
# 收集静态文件
python manage.py collectstatic --noinput

# 使用 Gunicorn 启动
gunicorn mysite.wsgi --bind 127.0.0.1:8000 --workers 3 --timeout 60
```

### Gunicorn 常用管理命令（systemd）

如果使用系统服务 `gunicorn.service`，可以通过以下命令管理：

```bash
# 查看状态
sudo systemctl status gunicorn

# 代码更新后重启
sudo systemctl restart gunicorn

# 停止服务
sudo systemctl stop gunicorn

# 开启/关闭开机自启
sudo systemctl enable gunicorn
sudo systemctl disable gunicorn

# 查看日志（错误 / 访问）
sudo tail -f /var/log/gunicorn-error.log
sudo tail -f /var/log/gunicorn-access.log
```

## 项目结构

```
mysite/
├── mysite/              # Django 项目配置
│   ├── settings.py      # 主配置文件
│   ├── urls.py          # URL 路由
│   └── admin_config.py  # Unfold 后台回调
├── posts/               # 博客应用
│   ├── models.py        # Post 模型（含 content_hash）
│   ├── views.py         # 视图（含上传逻辑）
│   ├── admin.py         # 后台（支持 ZIP 上传）
│   ├── feeds.py         # RSS 订阅
│   └── utils.py         # Markdown 渲染、TOC 生成
├── templates/           # HTML 模板
│   ├── base.html        # 基础布局（暗黑模式）
│   ├── detail.html      # 文章页（TOC、Giscus）
│   └── admin/           # 自定义后台模板
├── content/             # 示例 Markdown 文章
├── media/               # 上传的图片
└── staticfiles/         # 收集的静态文件
```

## Markdown 格式

```markdown
---
title: "文章标题"
date: 2024-01-15
tags: ["python", "django", "教程"]
category: tech
description: "文章简介"
slug: custom-url-slug
---

这里是正文内容...

## 二级标题

### 三级标题

![图片说明](./assets/image.png)
```

## 环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `SECRET_KEY` | Django 密钥 | (开发密钥) |
| `DEBUG` | 调试模式 | `False` |
| `DATABASE_URL` | 数据库连接 URL | SQLite |
| `GISCUS_REPO` | GitHub 仓库（评论用） | - |
| `GISCUS_REPO_ID` | Giscus 仓库 ID | - |
| `GISCUS_CATEGORY_ID` | Giscus 分类 ID | - |

## API 端点

| 端点 | 说明 |
|------|------|
| `/techblog/` | 首页文章列表 |
| `/techblog/<slug>/` | 文章详情页 |
| `/techblog/tech/` | 技术文章分类 |
| `/techblog/paper/` | 论文笔记分类 |
| `/techblog/tags/<tag>/` | 按标签筛选 |
| `/techblog/search/?q=` | 搜索结果 |
| `/rss.xml` | RSS 订阅 |
| `/admin/` | 管理后台 |

## 许可证

MIT License

## 作者

Egbert Lannister

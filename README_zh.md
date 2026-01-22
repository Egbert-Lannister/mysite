# Egbert's Blog

一个基于 Django 构建的现代技术博客，支持 Markdown 写作、标签管理、全文搜索和 RSS 订阅。

## 功能特性

- **Markdown 支持** - 使用带 YAML front matter 的 Markdown 写作
- **标签系统** - 使用 django-taggit 管理文章标签
- **全文搜索** - PostgreSQL 全文搜索（支持 SQLite 回退）
- **RSS 订阅** - 自动生成 RSS 源，访问 `/rss.xml`
- **现代化后台** - 基于 django-unfold 的美观管理界面
- **响应式设计** - 基于 Tailwind CSS 的移动端适配

## 技术栈

- **后端**: Django 5.2, Gunicorn
- **数据库**: PostgreSQL（生产）/ SQLite（开发）
- **后台**: django-unfold
- **样式**: Tailwind CSS
- **静态文件**: WhiteNoise

## 快速开始

### 环境要求

- Python 3.11+
- PostgreSQL（可选，用于生产环境）

### 安装步骤

```bash
# 克隆仓库
git clone https://github.com/your-username/mysite.git
cd mysite

# 安装依赖
pip install -r requirements.txt

# 运行数据库迁移
python manage.py migrate

# 创建超级用户
python manage.py createsuperuser

# 启动开发服务器
python manage.py runserver
```

### 环境变量配置

```bash
SECRET_KEY=your-secret-key
DEBUG=True
DATABASE_URL=postgres://user:pass@localhost:5432/dbname
```

## 项目结构

```
mysite/
├── content/              # Markdown 源文件
│   ├── tech/            # 技术文章
│   └── paper/           # 论文笔记
├── mysite/              # Django 项目配置
├── posts/               # 博客文章应用
│   ├── admin.py         # 后台管理配置
│   ├── models.py        # 文章模型
│   ├── views.py         # 视图函数
│   └── feeds.py         # RSS 订阅
├── static/              # 静态文件
├── templates/           # HTML 模板
├── theme/               # Tailwind 主题应用
├── manage.py
├── requirements.txt
└── Procfile             # 部署配置
```

## 后台管理功能

访问 `/admin/` 进入管理后台：

- **内容管理**
  - 创建、编辑、删除文章
  - 直接上传 Markdown 文件
  - 批量发布/取消发布
  
- **快捷操作**
  - 上传 Markdown 文章
  - 预览文章列表

- **标签管理**
  - 创建和管理标签
  - 查看每个标签的文章数量

## Markdown 格式

文章支持 YAML front matter：

```markdown
---
title: "文章标题"
date: 2024-01-15
tags: ["python", "django"]
category: tech
description: "文章简介"
slug: custom-url-slug
---

这里是 Markdown 正文内容...
```

### 导入 Markdown 文件

```bash
# 从 content/ 文件夹导入所有 Markdown 文件
python manage.py loadmd
```

## 部署指南

### 使用 Gunicorn（生产环境）

```bash
# 安装 gunicorn
pip install gunicorn

# 启动服务
gunicorn mysite.wsgi:application --bind 0.0.0.0:8000
```

### Nginx 配置示例

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location /static/ {
        alias /path/to/mysite/staticfiles/;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### 收集静态文件

```bash
python manage.py collectstatic --noinput
```

## API 接口

| 路径 | 说明 |
|------|------|
| `/techblog/` | 首页文章列表 |
| `/techblog/tech/` | 技术文章 |
| `/techblog/paper/` | 论文笔记 |
| `/techblog/<slug>/` | 文章详情 |
| `/techblog/tags/<tag>/` | 按标签筛选 |
| `/techblog/search/?q=` | 搜索文章 |
| `/rss.xml` | RSS 订阅 |
| `/admin/` | 管理后台 |

## 开源协议

MIT License

## 参与贡献

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/amazing`)
3. 提交更改 (`git commit -m '添加新功能'`)
4. 推送分支 (`git push origin feature/amazing`)
5. 发起 Pull Request

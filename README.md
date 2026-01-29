# Egbert's TechBlog

[中文阅读](./README_zh.md)

A modern Django-powered technical blog with Markdown support, dark mode, and a beautiful UI.

## Features

- **Markdown + YAML Front Matter**: Write posts in Markdown with metadata support
- **ZIP Upload**: Upload articles with images as a ZIP package
- **Content Deduplication**: Automatic detection of duplicate content via SHA256 hash
- **Dark/Light Mode**: System-aware theme with manual toggle
- **Giscus Comments**: GitHub Discussions-based commenting system
- **Code Highlighting**: Syntax highlighting with one-click copy
- **Auto TOC**: Automatic table of contents for long articles
- **Social Sharing**: Copy link, WeChat QR code, Twitter/X sharing
- **RSS Feed**: Full RSS support for feed readers
- **Full-text Search**: PostgreSQL-powered search (with SQLite fallback)
- **Modern Admin**: Django Unfold admin interface

## Tech Stack

| Component | Technology |
|-----------|------------|
| Backend | Django 5.2 |
| Frontend | Tailwind CSS (CDN) |
| Database | PostgreSQL / SQLite |
| Admin UI | django-unfold |
| Tags | django-taggit |
| Static Files | WhiteNoise |
| Server | Gunicorn + Nginx |
| SSL | Let's Encrypt |

## Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL (optional, SQLite works for development)

### Installation

```bash
# Clone the repository
git clone https://github.com/Egbert-Lannister/mysite.git
cd mysite

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Start development server
python manage.py runserver
```

### Production Deployment

```bash
# Collect static files
python manage.py collectstatic --noinput

# Start with Gunicorn
gunicorn mysite.wsgi --bind 127.0.0.1:8000 --workers 3 --timeout 60
```

## Project Structure

```
mysite/
├── mysite/              # Django project settings
│   ├── settings.py      # Main configuration
│   ├── urls.py          # URL routing
│   └── admin_config.py  # Unfold admin callbacks
├── posts/               # Blog app
│   ├── models.py        # Post model with content_hash
│   ├── views.py         # Views including upload logic
│   ├── admin.py         # Admin with ZIP upload support
│   ├── feeds.py         # RSS feed
│   └── utils.py         # Markdown rendering, TOC generation
├── templates/           # HTML templates
│   ├── base.html        # Base layout with dark mode
│   ├── detail.html      # Article page with TOC, Giscus
│   └── admin/           # Custom admin templates
├── content/             # Sample markdown posts
├── media/               # Uploaded images
└── staticfiles/         # Collected static files
```

## Markdown Format

```markdown
---
title: "Article Title"
date: 2024-01-15
tags: ["python", "django", "tutorial"]
category: tech
description: "A brief description of the article"
slug: custom-url-slug
---

Your markdown content here...

## Heading 2

### Heading 3

![Image](./assets/image.png)
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SECRET_KEY` | Django secret key | (dev key) |
| `DEBUG` | Debug mode | `False` |
| `DATABASE_URL` | Database connection URL | SQLite |
| `GISCUS_REPO` | GitHub repo for comments | - |
| `GISCUS_REPO_ID` | Giscus repo ID | - |
| `GISCUS_CATEGORY_ID` | Giscus category ID | - |

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `/techblog/` | Homepage with article list |
| `/techblog/<slug>/` | Article detail page |
| `/techblog/tech/` | Tech category articles |
| `/techblog/paper/` | Paper category articles |
| `/techblog/tags/<tag>/` | Articles by tag |
| `/techblog/search/?q=` | Search results |
| `/rss.xml` | RSS feed |
| `/admin/` | Django admin |

## License

MIT License

## Author

Egbert Lannister

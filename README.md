# Egbert's Blog

A modern tech blog built with Django, featuring Markdown support, tag management, full-text search, and RSS feed.

## Features

- **Markdown Support** - Write posts in Markdown with YAML front matter
- **Tag System** - Organize posts with tags using django-taggit
- **Full-Text Search** - PostgreSQL full-text search (with SQLite fallback)
- **RSS Feed** - Auto-generated RSS feed at `/rss.xml`
- **Modern Admin UI** - Beautiful admin interface powered by django-unfold
- **Responsive Design** - Mobile-friendly frontend with Tailwind CSS

## Tech Stack

- **Backend**: Django 5.2, Gunicorn
- **Database**: PostgreSQL (production) / SQLite (development)
- **Admin**: django-unfold
- **Styling**: Tailwind CSS
- **Static Files**: WhiteNoise

## Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL (optional, for production)

### Installation

```bash
# Clone the repository
git clone https://github.com/your-username/mysite.git
cd mysite

# Install dependencies
pip install -r requirements.txt

# Run migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Start development server
python manage.py runserver
```

### Environment Variables

```bash
SECRET_KEY=your-secret-key
DEBUG=True
DATABASE_URL=postgres://user:pass@localhost:5432/dbname
```

## Project Structure

```
mysite/
├── content/              # Markdown source files
│   ├── tech/            # Tech articles
│   └── paper/           # Paper notes
├── mysite/              # Django project settings
├── posts/               # Blog posts app
│   ├── admin.py         # Admin configuration
│   ├── models.py        # Post model
│   ├── views.py         # View functions
│   └── feeds.py         # RSS feed
├── static/              # Static files
├── templates/           # HTML templates
├── theme/               # Tailwind theme app
├── manage.py
├── requirements.txt
└── Procfile             # Deployment config
```

## Admin Features

Access the admin panel at `/admin/` with these features:

- **Content Management**
  - Create, edit, delete posts
  - Upload Markdown files directly
  - Bulk publish/unpublish actions
  
- **Quick Actions**
  - Upload Markdown articles
  - Preview post list

- **Tag Management**
  - Create and manage tags
  - View post counts per tag

## Markdown Format

Posts support YAML front matter:

```markdown
---
title: "Your Post Title"
date: 2024-01-15
tags: ["python", "django"]
category: tech
description: "A brief description"
slug: custom-url-slug
---

Your markdown content here...
```

### Import Markdown Files

```bash
# Import all markdown files from content/ folder
python manage.py loadmd
```

## Deployment

### Using Gunicorn (Production)

```bash
# Install gunicorn
pip install gunicorn

# Run with gunicorn
gunicorn mysite.wsgi:application --bind 0.0.0.0:8000
```

### Nginx Configuration

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

### Collect Static Files

```bash
python manage.py collectstatic --noinput
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `/techblog/` | Homepage with post list |
| `/techblog/tech/` | Tech articles |
| `/techblog/paper/` | Paper notes |
| `/techblog/<slug>/` | Post detail |
| `/techblog/tags/<tag>/` | Posts by tag |
| `/techblog/search/?q=` | Search posts |
| `/rss.xml` | RSS feed |
| `/admin/` | Admin panel |

## License

MIT License

## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing`)
5. Open a Pull Request

# TechBlog (merged into existing mysite)

## Install
```bash
poetry install
poetry run python manage.py migrate
```

## Import Markdown
```bash
poetry run python manage.py loadmd
```

## Run
```bash
poetry run python manage.py runserver
```

## Notes
- Content in `content/tech/` and `content/paper/`
- Static in `static/`
- Templates in `templates/`
- RSS: `/rss.xml`

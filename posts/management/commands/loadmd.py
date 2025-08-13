import re
from datetime import datetime
from pathlib import Path

import yaml
from django.core.management.base import BaseCommand
from django.utils.text import slugify
from taggit.utils import parse_tags

from posts.models import Post

FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


class Command(BaseCommand):
    help = "Load markdown files from content/ into Post entries. Create or update by slug."

    def add_arguments(self, parser):
        parser.add_argument("base", nargs="?", default="content", help="Base content directory")

    def handle(self, *args, **options):
        base_dir = Path(options["base"]).resolve()
        if not base_dir.exists():
            self.stdout.write(self.style.ERROR(f"Directory not found: {base_dir}"))
            return

        md_files = list(base_dir.rglob("*.md"))
        created = 0
        updated = 0
        for file_path in md_files:
            text = file_path.read_text(encoding="utf-8")
            m = FRONT_MATTER_RE.match(text)
            if not m:
                self.stdout.write(self.style.WARNING(f"Skip (no front-matter): {file_path}"))
                continue
            fm_raw, body = m.groups()
            meta = yaml.safe_load(fm_raw) or {}

            title = meta.get("title") or file_path.stem
            date_str = meta.get("date")
            date = datetime.fromisoformat(str(date_str)) if date_str else datetime.now()
            tags = meta.get("tags", [])
            category = meta.get("category") or ("tech" if "tech" in str(file_path) else "paper")
            description = meta.get("description", "")
            slug = meta.get("slug") or slugify(title)

            post, is_created = Post.objects.update_or_create(
                slug=slug,
                defaults={
                    "title": title,
                    "description": description,
                    "content": body.strip(),
                    "date": date,
                    "category": category,
                    "published": True,
                },
            )

            post.tags.set(parse_tags(tags))

            if is_created:
                created += 1
            else:
                updated += 1

            self.stdout.write(self.style.SUCCESS(f"Upserted: {slug}"))

        self.stdout.write(f"Created: {created}, Updated: {updated}, Total files: {len(md_files)}")

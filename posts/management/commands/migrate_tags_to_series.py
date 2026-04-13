"""
Management command: migrate tag-based Series aggregation to FK-based.

For every Series that has a bound ``tag``, find all Posts tagged with that tag
and set ``Post.series`` + auto-assign ``series_order``.

Usage:
    python manage.py migrate_tags_to_series          # dry-run (default)
    python manage.py migrate_tags_to_series --apply   # actually write to DB
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from posts.models import Post, Series


class Command(BaseCommand):
    help = "Migrate tag-based Series aggregation to Post.series ForeignKey."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            default=False,
            help="Actually apply changes (default is dry-run).",
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        mode = "APPLY" if apply else "DRY-RUN"
        self.stdout.write(f"\n=== migrate_tags_to_series [{mode}] ===\n")

        series_with_tag = Series.objects.filter(tag__isnull=False).select_related("tag")
        if not series_with_tag.exists():
            self.stdout.write(self.style.WARNING("No Series with a bound tag found."))
            return

        total_linked = 0
        for series in series_with_tag:
            tag_name = series.tag.name
            posts = (
                Post.objects.filter(published=True, tags__name__in=[tag_name])
                .distinct()
                .order_by("date")
            )
            count = posts.count()
            self.stdout.write(
                f'\n  Series "{series.title}" (tag=#{tag_name}): '
                f"{count} posts found"
            )

            for order, post in enumerate(posts, start=1):
                already_linked = (
                    post.series_id == series.pk
                    and post.series_order == order
                )
                marker = " (already linked)" if already_linked else ""
                self.stdout.write(
                    f"    [{order}] {post.title} (slug={post.slug}){marker}"
                )
                if not already_linked and apply:
                    post.series = series
                    post.series_order = order
                    post.save(update_fields=["series", "series_order"])
                    total_linked += 1

        self.stdout.write(
            f"\n{'Applied' if apply else 'Would apply'}: "
            f"{total_linked} post(s) linked.\n"
        )
        if not apply:
            self.stdout.write(
                self.style.NOTICE(
                    "This was a dry-run. Re-run with --apply to commit changes."
                )
            )

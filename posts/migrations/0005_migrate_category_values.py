from django.db import migrations


def migrate_categories_forward(apps, schema_editor):
    Post = apps.get_model("posts", "Post")
    Post.objects.filter(category="tech").update(category="engineering")
    Post.objects.filter(category="paper").update(category="research")


def migrate_categories_backward(apps, schema_editor):
    Post = apps.get_model("posts", "Post")
    Post.objects.filter(category="engineering").update(category="tech")
    Post.objects.filter(category="research").update(category="paper")


class Migration(migrations.Migration):

    dependencies = [
        ("posts", "0004_series_tag_category_filter_post_category_update"),
    ]

    operations = [
        migrations.RunPython(migrate_categories_forward, migrate_categories_backward),
    ]

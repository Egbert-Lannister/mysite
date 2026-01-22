from django.apps import apps
from django.utils.text import slugify


def generate_unique_slug(base: str, *, instance_pk=None) -> str:
    raw = (base or "").strip()
    slug = slugify(raw)
    if not slug:
        slug = "post"

    Post = apps.get_model("posts", "Post")
    candidate = slug
    counter = 2
    qs = Post.objects.all()
    if instance_pk:
        qs = qs.exclude(pk=instance_pk)
    while qs.filter(slug=candidate).exists():
        candidate = f"{slug}-{counter}"
        counter += 1
    return candidate

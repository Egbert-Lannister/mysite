from django.db import models
from taggit.managers import TaggableManager
from django.utils.text import slugify

try:
    from django.contrib.postgres.indexes import GinIndex
    HAS_PG = True
except Exception:  # pragma: no cover
    GinIndex = None
    HAS_PG = False


class Post(models.Model):
    CATEGORY_CHOICES = (
        ("tech", "Tech"),
        ("paper", "Paper"),
    )

    title = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    content = models.TextField()
    date = models.DateTimeField()
    tags = TaggableManager(blank=True)
    category = models.CharField(max_length=32, choices=CATEGORY_CHOICES)
    published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date"]
        indexes = [GinIndex(fields=["title", "content"]) if HAS_PG else models.Index(fields=["date"])]

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)

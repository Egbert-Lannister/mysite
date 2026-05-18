from __future__ import annotations

import hashlib
import uuid

from django.db import models
from django.utils.text import slugify
from taggit.managers import TaggableManager

try:
    from django.contrib.postgres.indexes import GinIndex
    HAS_PG = True
except Exception:
    GinIndex = None
    HAS_PG = False


def generate_unique_slug(title: str, instance_pk: int | None = None) -> str:
    """Generate unique slug from title."""
    base_slug = slugify(title)
    if not base_slug:
        base_slug = uuid.uuid4().hex[:8]

    slug = base_slug
    counter = 1
    while Post.objects.filter(slug=slug).exclude(pk=instance_pk).exists():
        slug = f"{base_slug}-{counter}"
        counter += 1
    return slug


def generate_series_slug(title: str, instance_pk: int | None = None) -> str:
    """Generate unique slug for Series from title."""
    base_slug = slugify(title)
    if not base_slug:
        base_slug = uuid.uuid4().hex[:8]

    slug = base_slug
    counter = 1
    while Series.objects.filter(slug=slug).exclude(pk=instance_pk).exists():
        slug = f"{base_slug}-{counter}"
        counter += 1
    return slug


def compute_content_hash(content: str) -> str:
    """Compute SHA256 hash of content for deduplication."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def series_cover_upload_path(instance: "Series", filename: str) -> str:
    """Upload path: media/series_covers/<slug>/<filename>"""
    return f"series_covers/{instance.slug or 'tmp'}/{filename}"


class Series(models.Model):
    """
    独立系列/合集模型 — 通过 Post.series ForeignKey 关联文章。
    """

    title = models.CharField("系列标题", max_length=255, help_text="系列的显示名称")
    slug = models.SlugField("URL 别名", unique=True, help_text="用于生成系列链接")
    description = models.TextField("系列简介", blank=True, help_text="系列的详细介绍")
    cover_image = models.ImageField(
        "封面图片",
        upload_to=series_cover_upload_path,
        blank=True,
        help_text="上传封面图片（支持文件选择或粘贴上传）",
    )
    order = models.PositiveIntegerField(
        "排序权重", default=0,
        help_text="数字越小越靠前，用于首页展示顺序",
    )
    is_featured = models.BooleanField(
        "首页推荐", default=False,
        help_text="是否在首页显示",
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["order", "-created_at"]
        verbose_name = "系列"
        verbose_name_plural = "系列管理"

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = generate_series_slug(self.title, instance_pk=self.pk)
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        from django.urls import reverse
        return reverse("posts:series_detail", args=[self.slug])

    def get_posts(self):
        """Return published posts in this series, oldest first."""
        return (
            self.posts
            .filter(published=True)
            .order_by("date")
        )

    @property
    def post_count(self) -> int:
        return self.get_posts().count()

    @property
    def latest_post_date(self):
        latest = self.posts.filter(published=True).order_by("-date").first()
        return latest.date if latest else None


class Post(models.Model):
    CATEGORY_CHOICES = (
        ("engineering", "Engineering"),
        ("research", "Research"),
        ("notes", "Notes"),
        ("projects", "Projects"),
    )

    title = models.CharField("标题", max_length=255, help_text="文章标题")
    slug = models.SlugField("URL 别名", unique=True, help_text="用于生成文章链接")
    description = models.TextField("摘要", blank=True, help_text="文章简介")
    content = models.TextField("正文", help_text="Markdown 格式")
    content_hash = models.CharField(
        "内容哈希", max_length=64, blank=True, db_index=True,
        help_text="用于去重检测",
    )
    date = models.DateTimeField("发布日期")
    tags = TaggableManager(
        "标签", blank=True,
        help_text="多个标签请用英文逗号「,」分隔；单个标签内可以包含空格。示例：deep learning, pytorch",
    )
    category = models.CharField("分类", max_length=32, choices=CATEGORY_CHOICES)
    published = models.BooleanField("已发布", default=True)

    series = models.ForeignKey(
        Series,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="posts",
        verbose_name="所属系列",
        help_text="文章所属的系列/合集",
    )
    series_order = models.PositiveIntegerField(
        "系列内排序", null=True, blank=True,
        help_text="在系列中的顺序，数字越小越靠前",
    )

    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-date"]
        verbose_name = "文章"
        verbose_name_plural = "文章管理"
        indexes = [
            GinIndex(fields=["title", "content"], name="posts_post_title_gin")
            if HAS_PG
            else models.Index(fields=["date"], name="posts_post_date_idx")
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["series", "series_order"],
                name="unique_series_order",
                condition=models.Q(series__isnull=False, series_order__isnull=False),
            )
        ]

    def __str__(self) -> str:
        if self.series and self.series_order is not None:
            return f"[{self.series.title} #{self.series_order}] {self.title}"
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = generate_unique_slug(self.title, instance_pk=self.pk)
        if self.content:
            self.content_hash = compute_content_hash(self.content)
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        from django.urls import reverse
        return reverse("posts:post_detail", args=[self.slug])

    def get_series_prev(self) -> "Post | None":
        if not self.series:
            return None
        return (
            Post.objects.filter(
                series=self.series,
                date__lt=self.date,
                published=True,
            )
            .order_by("-date")
            .first()
        )

    def get_series_next(self) -> "Post | None":
        if not self.series:
            return None
        return (
            Post.objects.filter(
                series=self.series,
                date__gt=self.date,
                published=True,
            )
            .order_by("date")
            .first()
        )
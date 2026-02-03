import hashlib
from django.db import models
from django.utils.text import slugify
from taggit.managers import TaggableManager

try:
    from django.contrib.postgres.indexes import GinIndex
    HAS_PG = True
except Exception:
    GinIndex = None
    HAS_PG = False


def generate_unique_slug(title, instance_pk=None):
    """Generate unique slug from title"""
    from django.utils.text import slugify
    import uuid
    
    base_slug = slugify(title)
    if not base_slug:
        base_slug = uuid.uuid4().hex[:8]
    
    slug = base_slug
    counter = 1
    while Post.objects.filter(slug=slug).exclude(pk=instance_pk).exists():
        slug = f"{base_slug}-{counter}"
        counter += 1
    return slug


def generate_series_slug(title, instance_pk=None):
    """Generate unique slug for Series from title"""
    import uuid
    
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
    """Compute SHA256 hash of content for deduplication"""
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


class Series(models.Model):
    """
    合集/系列模型，用于组织有序的文章集合（如课程笔记、教程系列）
    """
    title = models.CharField("系列标题", max_length=255, help_text="系列的显示名称")
    slug = models.SlugField("URL 别名", unique=True, help_text="用于生成系列链接")
    description = models.TextField("系列简介", blank=True, help_text="系列的详细介绍")
    cover_image = models.URLField("封面图片", blank=True, help_text="系列封面图片 URL（可选）")
    order = models.PositiveIntegerField("排序权重", default=0, 
                                         help_text="数字越小越靠前，用于首页展示顺序")
    is_featured = models.BooleanField("首页推荐", default=False,
                                       help_text="是否在首页显示")
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

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('posts:series_detail', args=[self.slug])
    
    @property
    def post_count(self):
        """返回系列中已发布的文章数量"""
        return self.posts.filter(published=True).count()
    
    @property
    def latest_post_date(self):
        """返回系列中最新文章的发布日期"""
        latest = self.posts.filter(published=True).order_by('-date').first()
        return latest.date if latest else None


class Post(models.Model):
    CATEGORY_CHOICES = (
        ("tech", "技术文章"),
        ("paper", "论文笔记"),
    )

    title = models.CharField("标题", max_length=255, help_text="文章标题")
    slug = models.SlugField("URL 别名", unique=True, help_text="用于生成文章链接")
    description = models.TextField("摘要", blank=True, help_text="文章简介")
    content = models.TextField("正文", help_text="Markdown 格式")
    content_hash = models.CharField("内容哈希", max_length=64, blank=True, db_index=True,
                                     help_text="用于去重检测")
    date = models.DateTimeField("发布日期")
    tags = TaggableManager("标签", blank=True)
    category = models.CharField("分类", max_length=32, choices=CATEGORY_CHOICES)
    published = models.BooleanField("已发布", default=True)
    
    # Series fields
    series = models.ForeignKey(
        Series, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name="posts",
        verbose_name="所属系列",
        help_text="文章所属的系列/合集"
    )
    series_order = models.PositiveIntegerField(
        "系列内排序", 
        null=True, 
        blank=True,
        help_text="在系列中的顺序，数字越小越靠前"
    )
    
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-date"]
        verbose_name = "文章"
        verbose_name_plural = "文章管理"
        indexes = [
            GinIndex(fields=["title", "content"], name="posts_post_title_gin") if HAS_PG 
            else models.Index(fields=["date"], name="posts_post_date_idx")
        ]
        constraints = [
            # 同一 series 内 series_order 唯一（允许 null）
            models.UniqueConstraint(
                fields=['series', 'series_order'],
                name='unique_series_order',
                condition=models.Q(series__isnull=False, series_order__isnull=False)
            )
        ]

    def __str__(self) -> str:
        if self.series and self.series_order is not None:
            return f"[{self.series.title} #{self.series_order}] {self.title}"
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = generate_unique_slug(self.title, instance_pk=self.pk)
        # Auto compute content hash
        if self.content:
            self.content_hash = compute_content_hash(self.content)
        super().save(*args, **kwargs)
    
    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('posts:post_detail', args=[self.slug])
    
    def get_series_prev(self):
        """获取同系列的上一篇文章（基于 series_order）"""
        if not self.series or self.series_order is None:
            return None
        return Post.objects.filter(
            series=self.series,
            series_order__lt=self.series_order,
            published=True
        ).order_by('-series_order').first()
    
    def get_series_next(self):
        """获取同系列的下一篇文章（基于 series_order）"""
        if not self.series or self.series_order is None:
            return None
        return Post.objects.filter(
            series=self.series,
            series_order__gt=self.series_order,
            published=True
        ).order_by('series_order').first()
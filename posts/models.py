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


def compute_content_hash(content: str) -> str:
    """Compute SHA256 hash of content for deduplication"""
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


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

    def __str__(self) -> str:
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

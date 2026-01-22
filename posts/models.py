from django.db import models
from taggit.managers import TaggableManager
from .utils import generate_unique_slug

try:
    from django.contrib.postgres.indexes import GinIndex
    HAS_PG = True
except Exception:  # pragma: no cover
    GinIndex = None
    HAS_PG = False


class Post(models.Model):
    CATEGORY_CHOICES = (
        ("tech", "技术文章"),
        ("paper", "论文笔记"),
    )

    title = models.CharField("标题", max_length=255, help_text="文章标题，将显示在列表和详情页")
    slug = models.SlugField("URL 别名", unique=True, help_text="用于生成文章链接，只能包含字母、数字和连字符")
    description = models.TextField("摘要", blank=True, help_text="文章简介，显示在列表页和 SEO 描述中")
    content = models.TextField("正文", help_text="支持 Markdown 格式")
    date = models.DateTimeField("发布日期", help_text="文章的发布时间")
    tags = TaggableManager("标签", blank=True, help_text="文章标签，用于分类和搜索")
    category = models.CharField("分类", max_length=32, choices=CATEGORY_CHOICES, help_text="选择文章类别")
    published = models.BooleanField("已发布", default=True, help_text="取消勾选将隐藏文章")
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-date"]
        indexes = [GinIndex(fields=["title", "content"]) if HAS_PG else models.Index(fields=["date"])]
        verbose_name = "文章"
        verbose_name_plural = "文章管理"

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = generate_unique_slug(self.title, instance_pk=self.pk)
        super().save(*args, **kwargs)

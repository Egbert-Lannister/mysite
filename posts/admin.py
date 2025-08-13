from django.contrib import admin
from .models import Post


@admin.action(description="Publish selected posts")
def make_published(modeladmin, request, queryset):
    queryset.update(published=True)


@admin.action(description="Unpublish selected posts")
def make_unpublished(modeladmin, request, queryset):
    queryset.update(published=False)


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ("title", "date", "category", "published")
    list_filter = ("category", "published", "tags")
    search_fields = ("title", "description", "content")
    prepopulated_fields = {"slug": ("title",)}
    actions = [make_published, make_unpublished]

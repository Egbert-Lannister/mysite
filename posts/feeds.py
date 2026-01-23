from django.contrib.syndication.views import Feed
from django.urls import reverse
from .models import Post


class LatestPostsFeed(Feed):
    title = "Egbert's TechBlog"
    link = "/techblog/"
    description = "分享技术见解与学习心得"

    def items(self):
        return Post.objects.filter(published=True).order_by("-date")[:30]

    def item_title(self, item: Post):
        return item.title

    def item_description(self, item: Post):
        return item.description or item.title

    def item_link(self, item: Post):
        return reverse("posts:post_detail", args=[item.slug])

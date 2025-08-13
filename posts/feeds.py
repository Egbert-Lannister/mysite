from django.contrib.syndication.views import Feed
from django.urls import reverse
from .models import Post


class LatestPostsFeed(Feed):
    title = "TechBlog RSS"
    link = "/rss.xml"
    description = "Latest posts"

    def items(self):
        return Post.objects.filter(published=True).order_by("-date")[:30]

    def item_title(self, item: Post):
        return item.title

    def item_description(self, item: Post):
        return item.description

    def item_link(self, item: Post):
        return reverse("post_detail", args=[item.slug])

"""
Admin configuration callbacks for Django Unfold
"""

def environment_callback(request):
    """Returns environment information for the admin header."""
    return ["生产环境", "success"]


def post_count_callback(request):
    """Returns the count of published posts for the sidebar badge."""
    from posts.models import Post
    return Post.objects.filter(published=True).count()

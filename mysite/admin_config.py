"""
Admin configuration callbacks for Django Unfold.
"""

from django.conf import settings
from django.core.cache import cache
from django.urls import reverse


def environment_callback(request):
    """Returns environment information for the admin header."""
    return ["本地开发", "success"] if settings.DEBUG else ["生产环境", "warning"]


def post_count_callback(request):
    """Returns the count of published posts for the sidebar badge."""
    from posts.models import Post

    return cache.get_or_set(
        "admin:published-post-count",
        lambda: Post.objects.filter(published=True).exclude(category="projects").count(),
        timeout=60,
    )


def _can_view(user, model_name):
    """Match Django admin's default view-or-change visibility rules."""
    return user.has_perm(f"posts.view_{model_name}") or user.has_perm(
        f"posts.change_{model_name}"
    )


def _content_queryset(post_model, *, can_view_posts, can_view_projects):
    """Limit the shared Post table to the content types the user may see."""
    queryset = post_model.objects.all()
    if can_view_posts and can_view_projects:
        return queryset
    if can_view_posts:
        return queryset.exclude(category="projects")
    if can_view_projects:
        return queryset.filter(category="projects")
    return queryset.none()


def _content_item(post):
    """Serialize only the fields and admin route needed by the dashboard."""
    is_project = post.category == "projects"
    model_name = "project" if is_project else "post"
    return {
        "title": post.title or "（无标题）",
        "url": reverse(f"admin:posts_{model_name}_change", args=(post.pk,)),
        "kind": "项目" if is_project else "文章",
        "status": "已发布" if post.published else "草稿",
        "timestamp": post.date if post.published else post.updated_at,
        "time_label": "发布于" if post.published else "更新于",
    }


def dashboard_callback(request, context):
    """Build a permission-scoped content dashboard from live ORM data."""
    from posts.models import Post, Project, Series

    user = request.user
    can_view_posts = _can_view(user, "post")
    can_view_projects = _can_view(user, "project")
    can_view_series = _can_view(user, "series")

    metrics = []
    if can_view_posts:
        metrics.append({
            "key": "articles",
            "label": "Articles",
            "count": Post.objects.exclude(category="projects").count(),
            "description": "全部文章，不含项目",
            "url": reverse("admin:posts_post_changelist"),
        })
    if can_view_projects:
        metrics.append({
            "key": "projects",
            "label": "Projects",
            "count": Project.objects.count(),
            "description": "全部项目，含未发布",
            "url": reverse("admin:posts_project_changelist"),
        })
    if can_view_series:
        metrics.append({
            "key": "series",
            "label": "Series",
            "count": Series.objects.count(),
            "description": "全部内容系列",
            "url": reverse("admin:posts_series_changelist"),
        })

    content_queryset = _content_queryset(
        Post,
        can_view_posts=can_view_posts,
        can_view_projects=can_view_projects,
    )
    if can_view_posts or can_view_projects:
        if can_view_posts and can_view_projects:
            draft_description = "文章与项目的未发布内容"
            draft_key = "all"
        elif can_view_posts:
            draft_description = "未发布文章，不含项目"
            draft_key = "articles"
        else:
            draft_description = "未发布项目"
            draft_key = "projects"
        metrics.append({
            "key": "drafts",
            "label": "Drafts",
            "count": content_queryset.filter(published=False).count(),
            "description": draft_description,
            "url": None,
            "scope": draft_key,
        })

    recent_content = [
        _content_item(post)
        for post in content_queryset.filter(published=True).only(
            "pk", "title", "category", "published", "date", "updated_at"
        ).order_by("-date", "-pk")[:6]
    ]
    recent_drafts = [
        _content_item(post)
        for post in content_queryset.filter(published=False).only(
            "pk", "title", "category", "published", "date", "updated_at"
        ).order_by("-updated_at", "-pk")[:6]
    ]

    quick_actions = []
    if user.has_perm("posts.add_post"):
        quick_actions.extend([
            {
                "label": "新建文章",
                "description": "从空白编辑器开始",
                "url": reverse("admin_upload_blank"),
                "icon": "edit_note",
            },
            {
                "label": "导入文章",
                "description": "上传 Markdown 或 ZIP",
                "url": reverse("admin_upload"),
                "icon": "upload_file",
            },
        ])
    if user.has_perm("posts.add_project"):
        quick_actions.extend([
            {
                "label": "新建项目",
                "description": "创建项目与实验内容",
                "url": reverse("admin_project_upload_blank"),
                "icon": "edit_note",
            },
            {
                "label": "导入项目",
                "description": "导入正文与项目媒体",
                "url": reverse("admin_project_upload"),
                "icon": "upload_file",
            },
        ])
    if user.has_perm("posts.add_series"):
        quick_actions.append({
            "label": "新建系列",
            "description": "组织连续的专题内容",
            "url": reverse("admin:posts_series_add"),
            "icon": "collections_bookmark",
        })

    draft_links = []
    if can_view_posts:
        draft_links.append({
            "label": "全部文章草稿",
            "url": f"{reverse('admin:posts_post_changelist')}?published__exact=0",
        })
    if can_view_projects:
        draft_links.append({
            "label": "全部项目草稿",
            "url": f"{reverse('admin:posts_project_changelist')}?published__exact=0",
        })

    context.update({
        "title": "内容工作台",
        "dashboard_metrics": metrics,
        "dashboard_recent_content": recent_content,
        "dashboard_recent_drafts": recent_drafts,
        "dashboard_quick_actions": quick_actions,
        "dashboard_draft_links": draft_links,
        "dashboard_has_content_access": can_view_posts or can_view_projects,
    })
    return context

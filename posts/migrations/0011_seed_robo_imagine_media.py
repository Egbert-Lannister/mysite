"""Associate the existing Robo-Imagine assets with that project only.

Only explicitly named files are copied. The reference repository is never exposed
as a static directory and none of its scripts are executed or embedded.
"""
from pathlib import Path

from django.conf import settings
from django.core.files.base import File
from django.core.files.storage import default_storage
from django.db import migrations


MEDIA = [
    {"file": "principle_flow.png", "kind": "image", "section": "method", "title": "Working principle", "group": "", "order": 1},
    {"file": "framework.png", "kind": "image", "section": "framework", "title": "Model framework", "group": "", "order": 1},
    {"file": "bridge_pie.png", "kind": "image", "section": "results", "title": "Experiment chart (a)", "group": "Generalization analysis", "order": 1},
    {"file": "bridge_radar.png", "kind": "image", "section": "results", "title": "Experiment chart (b)", "group": "Generalization analysis", "order": 2},
    {"file": "rt1_pie.png", "kind": "image", "section": "results", "title": "Experiment chart (c)", "group": "Generalization analysis", "order": 3},
    {"file": "rt1_radar.png", "kind": "image", "section": "results", "title": "Experiment chart (d)", "group": "Generalization analysis", "order": 4},
    {"file": "rt1_seen_pick_rxbar_chocolate.mp4", "kind": "video", "section": "results", "title": "Pick the chocolate bar", "group": "RT-1 · Seen tasks", "order": 1},
    {"file": "rt1_seen_open_middle_drawer.mp4", "kind": "video", "section": "results", "title": "Open the middle drawer", "group": "RT-1 · Seen tasks", "order": 2},
    {"file": "bridge_seen_open_microwave.mp4", "kind": "video", "section": "results", "title": "Open the microwave", "group": "Bridge · Seen tasks", "order": 1},
    {"file": "real_bridge_move_orange_near_apple.mp4", "kind": "video", "section": "results", "title": "Move orange near apple", "group": "Real world", "order": 1},
    {"file": "sim_pick_apple.mp4", "kind": "video", "section": "results", "title": "Pick up the apple", "group": "Simulation", "order": 1},
    {"file": "vlm_0000000_sample0.mp4", "kind": "video", "section": "comparison", "title": "w/ VLM", "group": "VLM comparison · Sample 0", "order": 2, "description": "place the blue cup over the black cup"},
    {"file": "vlm_0000000_sample0_noVLM.mp4", "kind": "video", "section": "comparison", "title": "w/o VLM", "group": "VLM comparison · Sample 0", "order": 1, "description": "place the blue cup over the black cup"},
]


def seed_media(apps, schema_editor):
    Post = apps.get_model("posts", "Post")
    ProjectMedia = apps.get_model("posts", "ProjectMedia")
    project = Post.objects.filter(slug="robo-imagine", category="projects").first()
    if not project:
        return

    # This exact value was a placeholder, not a paper URL. Keep real links intact.
    if project.paper_url.rstrip("/") in {"https://arxiv.org", "http://arxiv.org"}:
        project.paper_url = ""
        project.save(update_fields=["paper_url"])

    static_dir = Path(settings.BASE_DIR) / "static" / "projects" / "robo-imagine"
    reference_dir = Path(settings.BASE_DIR) / "Robo-Imagine" / "img"
    for item in MEDIA:
        if ProjectMedia.objects.filter(
            project=project,
            title=item["title"],
            group_name=item["group"],
            section=item["section"],
        ).exists():
            continue
        source = static_dir / item["file"]
        if not source.is_file():
            source = reference_dir / item["file"]
        if not source.is_file():
            continue
        storage_name = f"projects/robo-imagine/imported/{source.name}"
        if not default_storage.exists(storage_name):
            with source.open("rb") as stream:
                storage_name = default_storage.save(storage_name, File(stream, name=source.name))
        ProjectMedia.objects.create(
            project=project,
            file=storage_name,
            kind=item["kind"],
            section=item["section"],
            title=item["title"],
            description=item.get("description", ""),
            group_name=item["group"],
            order=item["order"],
        )


class Migration(migrations.Migration):
    dependencies = [("posts", "0010_project_alter_post_demo_url_alter_post_github_url_and_more")]
    operations = [migrations.RunPython(seed_media, migrations.RunPython.noop)]

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("posts", "0003_add_series_model"),
        ("taggit", "0006_rename_taggeditem_content_type_object_id_taggit_tagg_content_8fc721_idx"),
    ]

    operations = [
        migrations.AddField(
            model_name="series",
            name="tag",
            field=models.ForeignKey(
                blank=True,
                help_text="绑定一个 Tag，系列详情页将自动聚合包含该 Tag 的所有文章",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="taggit.tag",
                verbose_name="关联标签",
            ),
        ),
        migrations.AddField(
            model_name="series",
            name="category_filter",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "不限"),
                    ("engineering", "Engineering"),
                    ("research", "Research"),
                    ("notes", "Notes"),
                    ("projects", "Projects"),
                ],
                default="",
                help_text="可选：只显示该分类下的文章，留空则不限分类",
                max_length=32,
                verbose_name="分类过滤",
            ),
        ),
        migrations.AlterField(
            model_name="post",
            name="category",
            field=models.CharField(
                choices=[
                    ("engineering", "Engineering"),
                    ("research", "Research"),
                    ("notes", "Notes"),
                    ("projects", "Projects"),
                ],
                max_length=32,
                verbose_name="分类",
            ),
        ),
    ]

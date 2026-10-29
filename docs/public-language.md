# 前台中英文切换

页头的「中文 / EN」按钮通过 Django `set_language` POST 接口切换语言，保留当前路径和查询参数。语言 Cookie 仅用于 `/techblog/`，有效期一年；没有选择时默认英文。后台仍使用原来的中文配置。

导航、按钮、分类、空状态、分页、日期、数量单位和 JavaScript 提示均随语言切换。文章和项目的标题、摘要、正文、标签、作者自定义素材分组名保留原文；此功能不自动翻译创作内容。

模板使用 Django `translate` / `blocktranslate`，日期与数量单位使用 `public_i18n` 模板库。中文文案维护在 `locale/zh_Hans/LC_MESSAGES/django.po`，对应的 `django.mo` 一并纳入版本控制，部署无需现场编译。

更新 `.po` 后，在已安装 GNU gettext 的环境运行 `python manage.py compilemessages -l zh_Hans` 并重启服务。分类说明、空状态参数、数量单位和 JavaScript 标签含动态消息，更新文案时也要同步维护字典，不能仅依赖模板自动提取。

验证：`python manage.py test posts.test_public_language`。测试覆盖切换持久化、搜索参数、全部主要前台页面、原文保留、日期、CSRF、跳转安全和后台语言隔离。

中文使用本地 Noto Sans SC 可变字体，许可证和来源见 `static/fonts/noto-sans-sc/`。界面子集约 67 KiB，常用汉字子集约 1.8 MiB，按字符需求加载并缓存；其余字符使用系统后备字体。更新中文文案后，可用 `scripts/build_chinese_font.py` 从官方源字体重新生成子集及 CSS。构建依赖 fonttools 和 brotli，运行站点无需安装它们。

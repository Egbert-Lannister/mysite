# HTML project page template

`project-page-template.html` is a semantic, English-language starting point for a project ZIP import. Rename it to **`index.html`**, replace the example writing with your own, and provide every referenced file under `assets/` with a supported format. The sample media names are intentional placeholders, not files shipped with this template.

The template contains no JavaScript, external stylesheet, inline CSS, or autoplay behavior. **The website's own CSS takes over presentation after import.** Its small set of `project-html-*` classes provides stable layout hooks rather than author-supplied styling.

## Package layout

Use either a root-level document:

```text
index.html
assets/
  method-overview.png
  experiment-results.png
  ablation-results.webp
  rollout-demo.mp4
```

or the same files inside one shared top-level directory:

```text
my-project/
  index.html
  assets/
    method-overview.png
    experiment-results.png
    ablation-results.webp
    rollout-demo.mp4
```

The ZIP must contain exactly one HTML file, named `index.html`. Do not add JavaScript, CSS, nested ZIPs, extra HTML documents, or other unsupported files to the import archive. Keep notes and documentation outside the ZIP.

All media references must be relative to `index.html`, and every referenced file must exist with the matching media kind. External URLs, absolute paths, `data:`, `blob:`, `file:`, `javascript:`, `srcset`, and path traversal are not accepted for media. Supported images are PNG, JPEG, GIF, and WebP; supported videos are MP4 and WebM, subject to the site's configured upload count and size limits. Images are verified with Pillow and their dimensions and byte size are recorded.

Only referenced media are staged for import. Unreferenced media produce warnings and are excluded. Remove unused files from the archive when possible.

## Document and figure metadata

- `<title>` supplies the project title. A first `<h1>` is the fallback.
- `<meta name="description" content="...">` supplies the project summary. The meta element is parsed for metadata and is not emitted into public project HTML.
- A figure can declare `data-section`, `data-group`, `data-title`, and `data-description`.
- Supported media sections are `method`, `framework`, `results`, `comparison`, and `gallery`. Method/framework media must be images. The default is `gallery` for images and `results` for videos.
- `data-group` is the experiment, dataset, or comparison group shown by the existing project-media workflow.
- `data-title` takes precedence over the figcaption as the media title. The filename stem is the final fallback.
- All `data-*` attributes are removed from sanitized public HTML.

The example demonstrates Overview (the opening section), Method, Experiments, and Conclusion. Tables, lists, blockquotes, preformatted code, figures, captions, images, videos, and ordinary HTTP/HTTPS links can be used without adding active content.

## Sanitization and playback

The importer removes scripts, styles, frames, embeds, loading elements, event handlers, arbitrary classes, and unsupported attributes. Only these layout classes survive:

- `project-html-section`
- `project-html-grid`
- `project-html-media`
- `project-html-wide`
- `project-html-caption`

Videos are always emitted with `controls playsinline preload="metadata"`; autoplay, loops, posters, and other playback attributes are discarded. Links using `target="_blank"` receive `rel="noopener noreferrer"`.

Sanitization is intentionally repeatable. After the files are saved through the normal project-media workflow, package-relative media references can be replaced with their final local `MEDIA_URL` values without retaining upload-time metadata or active HTML.

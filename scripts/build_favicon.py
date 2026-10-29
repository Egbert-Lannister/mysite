"""Build the Open Folio favicon and raster fallbacks from shared geometry.

Run with Pillow installed: python scripts/build_favicon.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "static" / "images"
BACKGROUND = "#172E28"
PAGES = (
    ("#F5F0E5", ((14, 21), (28, 29), (28, 49), (14, 41))),
    ("#D5E8DD", ((35, 24), (49, 16), (49, 41), (35, 49))),
)
LIGHT = "#70D5AF"


def render(size):
    scale = max(4, 1024 // size)
    canvas_size = size * scale
    unit = canvas_size / 64
    image = Image.new("RGBA", (canvas_size, canvas_size))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, canvas_size - 1, canvas_size - 1), radius=15 * unit, fill=BACKGROUND)
    for color, points in PAGES:
        draw.polygon([(x * unit, y * unit) for x, y in points], fill=color)
    draw.ellipse(tuple(v * unit for v in (45.5, 5.5, 52.5, 12.5)), fill=LIGHT)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def main():
    polygons = "\n".join(
        f'  <polygon points="{" ".join(f"{x},{y}" for x, y in points)}" fill="{color}"/>'
        for color, points in PAGES
    )
    (OUT / "favicon.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-labelledby="title desc">\n'
        '  <title id="title">TechBlog — Open Folio</title>\n'
        '  <desc id="desc">Two open pages and a point of light: knowledge unfolding toward discovery.</desc>\n'
        f'  <rect width="64" height="64" rx="15" fill="{BACKGROUND}"/>\n'
        f'{polygons}\n'
        f'  <circle cx="49" cy="9" r="3.5" fill="{LIGHT}"/>\n'
        '</svg>\n', encoding="utf-8",
    )
    for size, filename in [(32, "favicon-32.png"), (192, "favicon-192.png"), (512, "favicon-512.png"), (180, "apple-touch-icon.png")]:
        render(size).save(OUT / filename)
    render(256).save(OUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("Built Open Folio SVG, PNG icons, and multi-resolution ICO.")


if __name__ == "__main__":
    main()

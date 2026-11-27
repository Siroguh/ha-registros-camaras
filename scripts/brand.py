"""Generate the project's simple chamber/shelf mark (no external artwork)."""

from pathlib import Path

from PIL import Image, ImageDraw

target = Path(__file__).resolve().parents[1] / "custom_components/registros_camaras/brand"
target.mkdir(exist_ok=True)
image = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((16, 16, 240, 240), radius=28, fill="#175c70")
draw.rectangle((58, 50, 198, 206), outline="white", width=12)
for y in (98, 152):
    draw.line((62, y, 194, y), fill="white", width=10)
for x in (85, 128, 171):
    draw.ellipse((x - 9, 68, x + 9, 86), fill="#a4dec8")
    draw.ellipse((x - 9, 120, x + 9, 138), fill="#a4dec8")
image.save(target / "icon.png")
image.save(target / "logo.png")

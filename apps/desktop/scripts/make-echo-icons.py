"""Draw Echo's face (white rounded square, two dark pill eyes) and write every app icon.

Same proportions as the website's EchoFace: corner radius 27%, eyes 11.5% x 20%
of the face, 15% apart. Run with Python 3 + Pillow:
    python apps/desktop/scripts/make-echo-icons.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
ICONS = ROOT / "desktop" / "src-tauri" / "icons"
WEB_PUBLIC = ROOT / "web" / "public"

FACE = (244, 244, 242, 255)
EYES = (7, 7, 7, 255)


def face(size: int, margin: float = 0.0) -> Image.Image:
    """Echo at `size` px; `margin` is the transparent border as a fraction of size."""
    scale = 4  # draw large, then downsample for smooth edges
    big = size * scale
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = round(big * margin)
    side = big - 2 * pad
    d.rounded_rectangle([pad, pad, pad + side - 1, pad + side - 1], radius=round(side * 0.27), fill=FACE)
    eye_w, eye_h, gap = side * 0.115, side * 0.2, side * 0.15
    cx, cy = big / 2, big / 2
    for direction in (-1, 1):
        x0 = cx + direction * (gap / 2 + eye_w / 2) - eye_w / 2
        y0 = cy - eye_h / 2
        d.rounded_rectangle([x0, y0, x0 + eye_w, y0 + eye_h], radius=eye_w / 2, fill=EYES)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    # Taskbar / window icons keep a small margin so the rounded corners read at 16-32 px.
    master = face(1024, margin=0.04)
    master.save(ICONS / "icon.png")
    for name, px in {
        "32x32.png": 32, "64x64.png": 64, "128x128.png": 128, "128x128@2x.png": 256,
        "Square30x30Logo.png": 30, "Square44x44Logo.png": 44, "Square71x71Logo.png": 71,
        "Square89x89Logo.png": 89, "Square107x107Logo.png": 107, "Square142x142Logo.png": 142,
        "Square150x150Logo.png": 150, "Square284x284Logo.png": 284, "Square310x310Logo.png": 310,
        "StoreLogo.png": 50,
    }.items():
        face(px, margin=0.04).save(ICONS / name)
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    face(256, margin=0.04).save(ICONS / "icon.ico", sizes=[(s, s) for s in sizes])
    face(1024, margin=0.04).save(ICONS / "icon.icns")
    # In-app logo and favicon: the face edge to edge.
    face(256).save(WEB_PUBLIC / "logo.png")
    print("wrote icons to", ICONS, "and", WEB_PUBLIC / "logo.png")


if __name__ == "__main__":
    main()

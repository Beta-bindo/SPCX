#!/usr/bin/env python3
"""Generate rounded app icons (PNG / ICNS / ICO) for Trade Assistant."""

from __future__ import annotations

import math
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "app" / "resources"
SIZE = 1024

# 欧冠标志风格：深色底 + 四角星拼成足球轮廓（星位为示意布局，非官方复刻）
_STAR_BALL = (
    (0.00, -0.52, 1.05),
    (-0.20, -0.34, 1.00),
    (0.20, -0.34, 1.00),
    (-0.40, -0.14, 0.96),
    (0.00, -0.14, 1.08),
    (0.40, -0.14, 0.96),
    (-0.52, 0.08, 0.94),
    (-0.26, 0.08, 1.00),
    (0.26, 0.08, 1.00),
    (0.52, 0.08, 0.94),
    (-0.38, 0.30, 0.98),
    (0.00, 0.30, 1.06),
    (0.38, 0.30, 0.98),
    (-0.22, 0.48, 0.94),
    (0.22, 0.48, 0.94),
    (-0.10, 0.60, 0.90),
    (0.10, 0.60, 0.90),
    (0.00, 0.70, 0.86),
)


def _lerp(a: int, b: int, t: float) -> int:
    return int(a + (b - a) * t)


def _rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size, size), radius=radius, fill=255)
    return mask


def _radial_bg(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx = cy = size // 2
    max_r = int(size * 0.72)
    for r in range(max_r, 0, -1):
        t = r / max_r
        color = (
            _lerp(8, 24, t),
            _lerp(22, 58, t),
            _lerp(58, 118, t),
            255,
        )
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
    return img


def _four_point_star(
    draw: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    outer: float,
    inner: float,
    fill: tuple[int, int, int, int],
) -> None:
    pts: list[tuple[float, float]] = []
    for i in range(8):
        angle = math.pi / 4 + i * math.pi / 4
        radius = outer if i % 2 == 0 else inner
        pts.append((cx + math.cos(angle) * radius, cy + math.sin(angle) * radius))
    draw.polygon(pts, fill=fill)


def render_master(size: int = SIZE) -> Image.Image:
    radius = int(size * 0.22)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    base = _radial_bg(size)
    inset = int(size * 0.06)
    gloss = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(gloss).rounded_rectangle(
        (inset, inset, size - inset, size - inset),
        radius=int(radius * 0.9),
        fill=(255, 255, 255, 22),
    )
    base = Image.alpha_composite(base, gloss)
    img.paste(base, (0, 0), _rounded_mask(size, radius))

    stars = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(stars)
    ball_r = size * 0.34
    cx = cy = size * 0.5
    star_outer = size * 0.052
    star_inner = star_outer * 0.34

    for nx, ny, scale in _STAR_BALL:
        sx = cx + nx * ball_r * 1.55
        sy = cy + ny * ball_r * 1.55
        outer = star_outer * scale
        inner = star_inner * scale
        _four_point_star(sdraw, sx, sy, outer * 1.15, inner, (255, 255, 255, 55))
        _four_point_star(sdraw, sx, sy, outer, inner, (248, 250, 252, 255))

    stars = stars.filter(ImageFilter.GaussianBlur(radius=max(1, int(size * 0.0015))))
    # Re-draw crisp stars on top of soft glow
    sharp = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sdraw2 = ImageDraw.Draw(sharp)
    for nx, ny, scale in _STAR_BALL:
        sx = cx + nx * ball_r * 1.55
        sy = cy + ny * ball_r * 1.55
        outer = star_outer * scale
        inner = star_inner * scale
        _four_point_star(sdraw2, sx, sy, outer, inner, (255, 255, 255, 245))

    img = Image.alpha_composite(img, stars)
    img = Image.alpha_composite(img, sharp)
    return img


def write_icns(path: Path) -> None:
    iconset = RES / "icon.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir(parents=True)
    spec = {
        "icon_16x16.png": 16,
        "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32,
        "icon_32x32@2x.png": 64,
        "icon_128x128.png": 128,
        "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256,
        "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512,
        "icon_512x512@2x.png": 1024,
    }
    master = render_master(SIZE)
    for name, px in spec.items():
        master.resize((px, px), Image.Resampling.LANCZOS).save(iconset / name, format="PNG")
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(path)], check=True)
    shutil.rmtree(iconset)


def write_ico(path: Path) -> None:
    master = render_master(SIZE)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [master.resize((s, s), Image.Resampling.LANCZOS) for s in sizes]
    images[0].save(
        path,
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=images[1:],
    )


def main() -> int:
    RES.mkdir(parents=True, exist_ok=True)
    png_path = RES / "icon.png"
    icns_path = RES / "icon.icns"
    ico_path = RES / "icon.ico"

    master = render_master(SIZE)
    master.save(png_path, format="PNG", optimize=True)
    write_ico(ico_path)
    if sys.platform == "darwin":
        write_icns(icns_path)
    print(f"Wrote {png_path}")
    print(f"Wrote {ico_path}")
    if icns_path.exists():
        print(f"Wrote {icns_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Generate the HACS brand assets for the go_gauge integration.

Pillow cannot rasterise SVG, so the gauge motif from
``custom_components/go_gauge/icons/icon.svg`` is redrawn programmatically
with :mod:`PIL.ImageDraw` (arcs, polygons). Everything is rendered at 4x
resolution and downscaled with LANCZOS to get smooth, anti-aliased edges.

Outputs (RGBA, fully transparent background):

* ``custom_components/go_gauge/brand/icon.png`` – 256x256, centred gauge.
* ``custom_components/go_gauge/brand/logo.png`` – 512x288 (16:9), same motif.

Only the standard library and Pillow are required. Run from the repo root::

    python3 scripts/generate_brand_assets.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

# --- Palette (taken verbatim from icons/icon.svg) -------------------------
BLUE = (0x41, 0xBD, 0xF5, 255)
ORANGE = (0xFF, 0x7B, 0x39, 255)

# --- Geometry in SVG user units (viewBox "0 0 24 24") ---------------------
VIEWBOX = 24.0
CENTER = (12.0, 13.0)  # gauge centre
OUTER_RADIUS = 9.0  # outer ring
RING_WIDTH = 2.0  # ring band thickness (outer 9 -> inner 7)
HUB_OUTER_RADIUS = 5.0  # inner hub band
HUB_WIDTH = 2.5  # hub band thickness (outer 5 -> inner 2.5)
# Outer ring is open at the bottom; ends sit at these math angles
# (0 deg = right, 90 deg = up, CCW).
RING_START_DEG = 212.0  # lower-left cap
RING_END_DEG = 328.0  # lower-right cap
# Needle polygon, same vertices as the SVG path.
NEEDLE = [(12.0, 11.0), (17.66, 15.15), (19.0, 20.0), (12.0, 17.0)]

# --- Rendering constants --------------------------------------------------
SUPERSAMPLE = 4
# Safe margin as a fraction of the target edge length (16px on a 256px icon).
MARGIN_RATIO = 16.0 / 256.0
# Alpha values below this are LANCZOS ringing halo rather than real coverage and
# get snapped to fully transparent.
ALPHA_FLOOR = 8

REPO_ROOT = Path(__file__).resolve().parents[1]
BRAND_DIR = REPO_ROOT / "custom_components" / "go_gauge" / "brand"


def _pil_angle(math_deg: float) -> float:
    """Convert a math angle (CCW, 0 = right) to a PIL angle (CW, 0 = 3 o'clock)."""
    return (-math_deg) % 360.0


def _render_gauge(square_px: int) -> Image.Image:
    """Render the gauge motif into a transparent ``square_px`` square."""
    canvas_px = square_px * SUPERSAMPLE
    margin = canvas_px * MARGIN_RATIO
    scale = (canvas_px - 2.0 * margin) / VIEWBOX

    image = Image.new("RGBA", (canvas_px, canvas_px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def point(x: float, y: float) -> tuple[float, float]:
        return (margin + x * scale, margin + y * scale)

    cx, cy = point(*CENTER)

    def radius(units: float) -> float:
        return units * scale

    # Outer open ring (drawn clockwise from the lower-left cap, over the top,
    # back down to the lower-right cap -> gap at the bottom).
    outer = radius(OUTER_RADIUS)
    draw.arc(
        (cx - outer, cy - outer, cx + outer, cy + outer),
        start=_pil_angle(RING_START_DEG),
        end=_pil_angle(RING_END_DEG) + 360.0,
        fill=BLUE,
        width=round(radius(RING_WIDTH)),
    )

    # Inner hub: upper half band only.
    hub = radius(HUB_OUTER_RADIUS)
    draw.arc(
        (cx - hub, cy - hub, cx + hub, cy + hub),
        start=180.0,
        end=360.0,
        fill=BLUE,
        width=round(radius(HUB_WIDTH)),
    )

    # Orange needle.
    draw.polygon([point(x, y) for x, y in NEEDLE], fill=ORANGE)

    return _finalize(image.resize((square_px, square_px), Image.LANCZOS))


def _finalize(image: Image.Image) -> Image.Image:
    """Drop the sub-perceptual LANCZOS ringing halo around the motif.

    ``resize`` leaves a ~1px ring of near-zero alpha outside the drawn shape;
    snapping those values to 0 keeps the background strictly transparent while
    the real anti-aliased edge (adjacent, higher-alpha pixels) stays smooth.
    """
    red, green, blue, alpha = image.split()
    alpha = alpha.point(lambda value: 0 if value < ALPHA_FLOOR else value)
    rgb = Image.merge("RGB", (red, green, blue))
    transparent = alpha.point(lambda value: 255 if value == 0 else 0)
    rgb = Image.composite(Image.new("RGB", image.size, (0, 0, 0)), rgb, transparent)
    return Image.merge("RGBA", (*rgb.split(), alpha))


def generate_icon(path: Path, size: int = 256) -> None:
    """Write the square brand icon."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _render_gauge(size).save(path, format="PNG")


def generate_logo(path: Path, width: int = 512, height: int = 288) -> None:
    """Write the loose 16:9 logo (icon only, no text)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    icon_px = 256
    gauge = _render_gauge(icon_px)
    logo = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    logo.alpha_composite(gauge, ((width - icon_px) // 2, (height - icon_px) // 2))
    logo.save(path, format="PNG")


def main() -> None:
    icon_path = BRAND_DIR / "icon.png"
    logo_path = BRAND_DIR / "logo.png"
    generate_icon(icon_path)
    generate_logo(logo_path)
    print(f"wrote {icon_path.relative_to(REPO_ROOT)}")
    print(f"wrote {logo_path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()

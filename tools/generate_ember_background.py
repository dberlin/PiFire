"""Render the ember radial-gradient dashboard background for each DSI resolution."""

import os
from PIL import Image

# (width, height) of every ember dashboard background this tool owns.
RESOLUTIONS = [(1280, 720), (1024, 600)]

# radial-gradient(120% 90% at 50% 118%, #241a12 0%, #16110d 42%, #0d0b09 100%)
STOPS = [(0.00, (0x24, 0x1A, 0x12)), (0.42, (0x16, 0x11, 0x0D)), (1.00, (0x0D, 0x0B, 0x09))]


def _lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _sample(frac):
    for i in range(len(STOPS) - 1):
        p0, c0 = STOPS[i]
        p1, c1 = STOPS[i + 1]
        if frac <= p1:
            t = 0 if p1 == p0 else (frac - p0) / (p1 - p0)
            return _lerp(c0, c1, t)
    return STOPS[-1][1]


def render(width, height):
    cx, cy = 0.50 * width, 1.18 * height
    rx, ry = 1.20 * width, 0.90 * height
    img = Image.new("RGB", (width, height))
    px = img.load()
    for y in range(height):
        for x in range(width):
            d = (((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2) ** 0.5
            px[x, y] = _sample(min(d, 1.0))
    return img


def out_path(width, height):
    base = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    return os.path.join(base, "static", "img", "display", f"background_ember_{width}x{height}.png")


def main():
    for width, height in RESOLUTIONS:
        path = out_path(width, height)
        render(width, height).save(path)
        print(f"Wrote {path}")


if __name__ == "__main__":
    main()

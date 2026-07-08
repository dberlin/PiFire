"""Render the ember radial-gradient dashboard background (1280x720)."""

import os
from PIL import Image

W, H = 1280, 720
# radial-gradient(120% 90% at 50% 118%, #241a12 0%, #16110d 42%, #0d0b09 100%)
CX, CY = 0.50 * W, 1.18 * H
RX, RY = 1.20 * W, 0.90 * H
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


def main():
	img = Image.new('RGB', (W, H))
	px = img.load()
	for y in range(H):
		for x in range(W):
			d = (((x - CX) / RX) ** 2 + ((y - CY) / RY) ** 2) ** 0.5
			px[x, y] = _sample(min(d, 1.0))
	base = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
	out = os.path.join(base, 'static', 'img', 'display', 'background_ember_1280x720.png')
	img.save(out)
	print(f'Wrote {out}')


if __name__ == '__main__':
	main()

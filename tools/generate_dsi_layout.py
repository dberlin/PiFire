#!/usr/bin/env python3
'''
Generate display/dsi_<W>x<H>t.json from display/dsi_800x480t.json.

Uniform fit-scale (min of per-axis ratios) with centering on the slack axis.
Re-run after changing the 800x480 layout:
    python tools/generate_dsi_layout.py
'''
import json
import copy
import os

SOURCE_W, SOURCE_H = 800, 480

# Target resolutions this generator owns. (width, height) of the landscape
# profile_1 canvas; profile_2 is the same canvas rotated.
RESOLUTIONS = [(1024, 768), (1280, 720)]

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SOURCE_PATH = os.path.join(BASE, 'display', 'dsi_800x480t.json')


def out_path(width, height):
    return os.path.join(BASE, 'display', f'dsi_{width}x{height}t.json')


def _scale_for(width, height):
    return min(width / SOURCE_W, height / SOURCE_H)


def _profile_dims(width, height):
    # profile_1 is the target as given (landscape); profile_2 is rotated.
    return {
        'profile_1': {'target': (width, height), 'source': (SOURCE_W, SOURCE_H)},
        'profile_2': {'target': (height, width), 'source': (SOURCE_H, SOURCE_W)},
    }


def _offsets(target, source, scale):
    tw, th = target
    sw, sh = source
    return round((tw - sw * scale) / 2), round((th - sh * scale) / 2)


def _scale_obj(obj, scale, xoff, yoff):
    if 'position' in obj:
        x, y = obj['position']
        obj['position'] = [round(x * scale + xoff), round(y * scale + yoff)]
    if 'size' in obj:
        w, h = obj['size']
        obj['size'] = [round(w * scale), round(h * scale)]


def build(width, height):
    with open(SOURCE_PATH) as f:
        src = json.load(f)
    data = copy.deepcopy(src)
    data['metadata']['name'] = f'dsi_{width}x{height}t'
    data['metadata']['screen_width'] = width
    data['metadata']['screen_height'] = height
    scale = _scale_for(width, height)
    dims = _profile_dims(width, height)
    for profile in ('profile_1', 'profile_2'):
        if profile not in data:
            continue
        xoff, yoff = _offsets(dims[profile]['target'], dims[profile]['source'], scale)
        prof = data[profile]
        for section in ('home', 'dash'):
            for obj in prof.get(section, []):
                _scale_obj(obj, scale, xoff, yoff)
        for section in ('menus', 'input'):
            for obj in prof.get(section, {}).values():
                _scale_obj(obj, scale, xoff, yoff)
    return data


def dumps(data):
    return json.dumps(data, indent=2) + '\n'


def main():
    for width, height in RESOLUTIONS:
        path = out_path(width, height)
        with open(path, 'w') as f:
            f.write(dumps(build(width, height)))
        print(f'Wrote {path}')


if __name__ == '__main__':
    main()

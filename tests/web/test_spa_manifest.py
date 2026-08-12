import json
import re

import pytest


def test_declared_manifest_is_served_and_its_icons_resolve(client):
    """Mirrors the favicon test: read the href out of the shipped shell and
    fetch exactly that, so a link the shell declares can never 404."""
    shell = client.get("/").get_data(as_text=True)
    m = re.search(r'rel="manifest"\s+href="([^"]+)"', shell)
    assert m, "index.html declared no rel=manifest"

    href = m.group(1)
    res = client.get(href)
    assert res.status_code == 200
    manifest = json.loads(res.get_data(as_text=True))
    for icon in manifest["icons"]:
        assert client.get(icon["src"]).status_code == 200, f"icon {icon['src']} is not served"

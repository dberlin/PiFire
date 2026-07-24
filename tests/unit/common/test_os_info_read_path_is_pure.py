"""The OS-info cache lives in the datastore, and reads must not write files.

`get_os_info()` probes /etc/os-release + `uname -m`. It used to persist the
result to `filepath` (default "os_info.json"), resolved against the process
CWD -- so where the cache landed depended on who started PiFire, and
`get_display_os_info()` (a READ helper for the admin page / mobile
system-info panel) silently created one on a cache miss. Running the test
suite dropped an os_info.json in the repo root that way.

The cache now lives in the datastore, which is the single source of truth for
live state; JSON files are exports, not the live copy.

These tests pin both halves of the contract:
  * nothing writes a CWD-relative os_info.json any more;
  * the cache still round-trips through the datastore, and a cache hit skips
    the live probe.
"""

import os
from unittest import mock

import pytest

from common import system
from common.datastore_accessors import load_os_info, store_os_info


@pytest.fixture
def in_tmp_cwd(tmp_path, monkeypatch):
    """Run with CWD inside tmp_path so any stray file write is visible."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_display_os_info_writes_no_file_on_cache_miss(ds, in_tmp_cwd):
    # Fresh datastore -> cache miss -> live probe fallback.
    assert load_os_info() == {}

    info = system.get_display_os_info()

    assert isinstance(info, dict) and info != {}
    assert not os.path.exists(in_tmp_cwd / "os_info.json"), (
        "os_info.json was created; the cache belongs in the datastore"
    )


def test_get_os_info_caches_to_the_datastore(ds, in_tmp_cwd):
    probed = system.get_os_info()

    assert load_os_info() == probed
    assert not os.path.exists(in_tmp_cwd / "os_info.json")


def test_get_os_info_persist_false_leaves_the_cache_untouched(ds, in_tmp_cwd):
    system.get_os_info(persist=False)

    assert load_os_info() == {}
    assert not os.path.exists(in_tmp_cwd / "os_info.json")


def test_display_os_info_uses_the_cache_and_skips_the_probe(ds):
    store_os_info({"PRETTY_NAME": "Cached OS", "ARCHITECTURE": "aarch64"})

    with mock.patch.object(system, "get_os_info") as probe:
        info = system.get_display_os_info()

    probe.assert_not_called()  # cache hit -- no live probe
    assert info["PRETTY_NAME"] == "Cached OS"
    assert info["BITS"] == "64-Bit"  # derived from ARCHITECTURE

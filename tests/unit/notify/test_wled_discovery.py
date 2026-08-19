"""Unit tests for notify.wled_discovery.

Discovers WLED devices via mDNS/zeroconf, in-process. The network/mDNS
boundary is mocked so no real multicast traffic or HTTP calls ever leave the
test process:

- ``notify.wled_discovery.Zeroconf`` / ``ServiceBrowser`` are patched to
  MagicMocks -- ``WLEDDiscovery.__enter__``/``discover_mdns_devices`` never
  touch a real socket.
- Per-device zeroconf callbacks (``add_service``) are driven directly with a
  fake ``zeroconf`` object whose ``get_service_info`` returns a hand-built
  ``ServiceInfo``-shaped stub (``addresses``/``port``/``properties``).
- ``requests.get`` (imported into the module as ``notify.wled_discovery.
  requests``) is patched for the HTTP detail-fetch step (``_get_device_info``).
- ``discover_network_scan``'s 254 real ``threading.Thread`` workers are
  replaced with a synchronous fake Thread so the IP-sweep logic runs
  deterministically and fast, without real OS threads or real network I/O.

Also documents four latent bugs, all since FIXED -- the tests below assert
the fixed behavior:

1. notify/wled_discovery.py:74 -- ``socket.inet_ntoa(info.addresses[0])``
   used to assume an IPv4 4-byte address and ignore every address past
   index 0. An IPv6-only mDNS announcement (or one with an empty
   ``addresses`` list) raised inside the broad ``try`` at add_service and
   was silently swallowed by ``except Exception`` -- the device was just
   dropped with no IPv6 fallback. Fixed: ``add_service`` now branches on the
   raw address length -- 4 bytes uses ``inet_ntoa``, 16 bytes uses
   ``socket.inet_ntop(AF_INET6, ...)``, and anything else (including empty)
   is skipped gracefully with a logged message instead of raising. See
   ``test_add_service_handles_ipv6_address_fixed`` and
   ``test_add_service_empty_addresses_skips_gracefully_fixed``.

2. notify/wled_discovery.py:102-121 -- ``_is_wled_device`` computed a
   name/TXT-record match against ``wled_patterns`` but then unconditionally
   ``return True`` regardless of the result -- dead code that could never
   return False. JUDGMENT: removed entirely rather than "fixed", since the
   real (and only) filter in effect was always the HTTP probe
   (``_get_device_info`` / ``device.online``) a few lines later -- making the
   dead matcher return real answers would have started excluding devices
   that are discovered today, which is a behavior change nobody asked for.
   Removing it preserves current behavior exactly while deleting misleading
   dead code. All ``_is_wled_device``-specific tests below were removed
   along with the method.

3. notify/wled_discovery.py:161-171 -- in ``_get_device_info``, if the
   primary parse succeeded partially (e.g. ``device.version``/``device.mac``/
   ``device.product`` already assigned from a good ``/json/info`` response)
   and then a *later* statement in the same ``try`` block raised (e.g.
   ``info_data["leds"].get(...)`` when ``leds`` isn't a dict), the except
   handler's HTTP fallback overwrote the already-correct ``device.product``
   with the placeholder ``"WLED (Unknown Version)"`` and reset
   ``device.led_count`` to 0 -- discarding good data already parsed. Fixed
   by only filling in the placeholder ``product`` when it's still unset, and
   no longer touching ``led_count`` in the fallback at all (it already
   defaults to 0 and must never be reset once parsed). See
   ``test_get_device_info_leds_not_a_dict_preserves_parsed_data_fixed``.

4. notify/wled_discovery.py:193-230 -- ``discover_network_scan`` accepted a
   ``network_range`` parameter but never used it: ``base_ip`` was hardcoded
   to ``"192.168.1."`` (the code even said "Could be made configurable").
   JUDGMENT: wired the parameter through (small change -- parse the first
   three octets out of ``network_range`` for ``base_ip``, falling back to
   ``192.168.1.`` if it doesn't look like a dotted-quad) rather than
   deleting the function, since it's real, independently-tested logic with
   its own test coverage already in this file -- removal would have thrown
   away more than it saved. It remains unreachable from ``discover_all``
   (that call is still commented out there), so this fix has no effect on
   the currently-live discovery path. See
   ``test_discover_network_scan_honors_network_range_parameter_fixed``.
"""

import socket
from unittest.mock import MagicMock, patch

from notify.wled_discovery import (
    WLEDDeviceInfo,
    WLEDDiscovery,
    discover_wled_devices,
)


def _fake_service_info(ip="192.168.1.50", port=80, properties=None):
    info = MagicMock()
    info.addresses = [socket.inet_aton(ip)]
    info.port = port
    info.properties = properties or {}
    return info


def _ok_json_response(json_data):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = json_data
    return resp


def _bad_status_response(status_code=404):
    resp = MagicMock()
    resp.status_code = status_code
    return resp


class ImmediateThread:
    """Synchronous stand-in for threading.Thread: runs target() on start()."""

    def __init__(self, target=None, args=(), **kwargs):
        self._target = target
        self._args = args

    def start(self):
        self._target(*self._args)

    def join(self):
        pass


# ---------------------------------------------------------------------------
# WLEDDeviceInfo
# ---------------------------------------------------------------------------


def test_device_info_defaults_and_to_dict():
    device = WLEDDeviceInfo("Bar Lights", "192.168.1.50", 8080)
    assert device.led_count == 0
    assert device.version == ""
    assert device.product == ""
    assert device.mac == ""
    assert device.online is False

    assert device.to_dict() == {
        "name": "Bar Lights",
        "ip": "192.168.1.50",
        "port": 8080,
        "led_count": 0,
        "version": "",
        "product": "",
        "mac": "",
        "online": False,
    }


def test_device_info_default_port():
    device = WLEDDeviceInfo("Bar Lights", "192.168.1.50")
    assert device.port == 80


# ---------------------------------------------------------------------------
# WLEDDiscovery context manager (__enter__ / __exit__)
# ---------------------------------------------------------------------------


def test_context_manager_creates_and_closes_zeroconf():
    with patch("notify.wled_discovery.Zeroconf") as mock_zc_cls:
        mock_zc = MagicMock()
        mock_zc_cls.return_value = mock_zc

        with WLEDDiscovery(discovery_timeout=5) as discovery:
            assert discovery.zeroconf is mock_zc
            discovery.browser = MagicMock()

        discovery.browser.cancel.assert_called_once()
        mock_zc.close.assert_called_once()


def test_context_manager_swallows_cancel_and_close_errors():
    """__exit__ must not propagate cleanup errors from browser.cancel() or
    zeroconf.close() -- both are wrapped in their own try/except Exception:
    pass."""
    with patch("notify.wled_discovery.Zeroconf") as mock_zc_cls:
        mock_zc = MagicMock()
        mock_zc.close.side_effect = RuntimeError("close boom")
        mock_zc_cls.return_value = mock_zc

        with WLEDDiscovery(discovery_timeout=5) as discovery:
            discovery.browser = MagicMock()
            discovery.browser.cancel.side_effect = RuntimeError("cancel boom")
        # No exception escaped the with-block -- that's the assertion.


def test_exit_with_no_browser_or_zeroconf_is_a_noop():
    discovery = WLEDDiscovery(discovery_timeout=5)
    # __enter__ never called: browser and zeroconf are still None.
    discovery.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# add_service -- the zeroconf callback / per-device parse path
# ---------------------------------------------------------------------------


def test_add_service_parses_and_appends_online_device():
    discovery = WLEDDiscovery()
    fake_zc = MagicMock()
    fake_zc.get_service_info.return_value = _fake_service_info(ip="192.168.1.77", port=80)

    info_json = {"ver": "0.14.0", "name": "Patio WLED", "mac": "aa:bb:cc:dd:ee:ff", "product": "WLED"}
    state_json = {"seg": [{"stop": 30}]}

    with patch("notify.wled_discovery.requests.get") as mock_get:
        mock_get.side_effect = [_ok_json_response(info_json), _ok_json_response(state_json)]
        discovery.add_service(fake_zc, "_http._tcp.local.", "Patio WLED._http._tcp.local.")

    assert len(discovery.discovered_devices) == 1
    device = discovery.discovered_devices[0]
    assert device.name == "Patio WLED"
    assert device.ip == "192.168.1.77"
    assert device.port == 80
    assert device.version == "0.14.0"
    assert device.mac == "aa:bb:cc:dd:ee:ff"
    assert device.led_count == 30
    assert device.online is True


def test_add_service_strips_mdns_suffix_from_default_name():
    """The device name defaults to the mDNS service name with the
    ._http._tcp.local. suffix stripped -- and stays that way if the HTTP
    /json/info response has "ver" (enough to go online) but no "name" key
    to override it."""
    discovery = WLEDDiscovery()
    fake_zc = MagicMock()
    fake_zc.get_service_info.return_value = _fake_service_info()

    info_json = {"ver": "0.14.0"}  # no "name" key -> mDNS-derived name wins
    state_json = {}

    with patch("notify.wled_discovery.requests.get") as mock_get:
        mock_get.side_effect = [_ok_json_response(info_json), _ok_json_response(state_json)]
        discovery.add_service(fake_zc, "_http._tcp.local.", "Living Room._http._tcp.local.")

    assert len(discovery.discovered_devices) == 1
    assert discovery.discovered_devices[0].name == "Living Room"


def test_add_service_skips_offline_device():
    """If the HTTP detail fetch never marks the device online, it is not
    added to discovered_devices."""
    discovery = WLEDDiscovery()
    fake_zc = MagicMock()
    fake_zc.get_service_info.return_value = _fake_service_info()

    with patch("notify.wled_discovery.requests.get") as mock_get:
        mock_get.return_value = _bad_status_response(404)
        discovery.add_service(fake_zc, "_http._tcp.local.", "Ghost._http._tcp.local.")

    assert discovery.discovered_devices == []


def test_add_service_none_info_is_a_noop():
    discovery = WLEDDiscovery()
    fake_zc = MagicMock()
    fake_zc.get_service_info.return_value = None

    discovery.add_service(fake_zc, "_http._tcp.local.", "Nothing._http._tcp.local.")

    assert discovery.discovered_devices == []


def test_add_service_empty_addresses_skips_gracefully_fixed():
    """FIXED LATENT BUG #1: an empty addresses list used to make
    socket.inet_ntoa(info.addresses[0]) raise IndexError, silently caught by
    the broad `except Exception` in add_service. Now add_service explicitly
    guards on an empty addresses list and returns early with a logged
    message -- same externally-visible outcome (device not discovered), but
    via an intentional guard instead of an accidental crash-and-swallow."""
    discovery = WLEDDiscovery()
    fake_zc = MagicMock()
    info = MagicMock()
    info.addresses = []  # no address advertised at all
    info.port = 80
    info.properties = {}
    fake_zc.get_service_info.return_value = info

    # Must not raise.
    discovery.add_service(fake_zc, "_http._tcp.local.", "NoAddress._http._tcp.local.")

    assert discovery.discovered_devices == []


def test_add_service_handles_ipv6_address_fixed():
    """FIXED LATENT BUG #1: a 16-byte (IPv6) address used to reach
    socket.inet_ntoa, which raises for anything other than a 4-byte
    address -- silently dropping IPv6-only devices. Now a 16-byte address is
    decoded via socket.inet_ntop(AF_INET6, ...) and the device is discovered
    normally."""
    discovery = WLEDDiscovery()
    fake_zc = MagicMock()
    info = MagicMock()
    info.addresses = [socket.inet_pton(socket.AF_INET6, "fe80::1234")]
    info.port = 80
    info.properties = {}
    fake_zc.get_service_info.return_value = info

    info_json = {"ver": "0.14.0", "name": "IPv6 WLED", "mac": "aa:bb:cc:dd:ee:ff", "product": "WLED"}
    state_json = {"seg": [{"stop": 30}]}

    with patch("notify.wled_discovery.requests.get") as mock_get:
        mock_get.side_effect = [_ok_json_response(info_json), _ok_json_response(state_json)]
        discovery.add_service(fake_zc, "_http._tcp.local.", "IPv6 WLED._http._tcp.local.")

    assert len(discovery.discovered_devices) == 1
    device = discovery.discovered_devices[0]
    assert device.ip == "fe80::1234"
    assert device.online is True


def test_add_service_unrecognized_address_length_skips_gracefully_fixed():
    """An address that's neither 4 nor 16 bytes (malformed/unexpected) is
    skipped gracefully rather than passed to inet_ntoa/inet_ntop, which
    would raise."""
    discovery = WLEDDiscovery()
    fake_zc = MagicMock()
    info = MagicMock()
    info.addresses = [b"\x01\x02\x03"]  # 3 bytes -- neither IPv4 nor IPv6
    info.port = 80
    info.properties = {}
    fake_zc.get_service_info.return_value = info

    discovery.add_service(fake_zc, "_http._tcp.local.", "Malformed._http._tcp.local.")

    assert discovery.discovered_devices == []


def test_add_service_suppresses_noneType_error_message():
    """The except branch has a special case: errors whose str() contains
    "NoneType" are suppressed entirely (not even printed) since they're
    common harmless zeroconf races. Cover both sides of that branch."""
    discovery = WLEDDiscovery()
    fake_zc = MagicMock()
    fake_zc.get_service_info.side_effect = TypeError("'NoneType' object is not subscriptable")

    # Must not raise, regardless of which message branch is taken.
    discovery.add_service(fake_zc, "_http._tcp.local.", "Racy._http._tcp.local.")

    assert discovery.discovered_devices == []


def test_remove_service_and_update_service_are_noops():
    discovery = WLEDDiscovery()
    fake_zc = MagicMock()
    # Must not raise and must not touch discovered_devices.
    discovery.remove_service(fake_zc, "_http._tcp.local.", "x")
    discovery.update_service(fake_zc, "_http._tcp.local.", "x")
    assert discovery.discovered_devices == []


# ---------------------------------------------------------------------------
# _is_wled_device -- REMOVED (LATENT BUG #2, dead code). See module
# docstring for the removal rationale; this test block used to exercise the
# now-deleted method and is intentionally gone along with it.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _get_device_info -- the HTTP detail-fetch step
# ---------------------------------------------------------------------------


def test_get_device_info_full_success_stop_segment():
    device = WLEDDeviceInfo("x", "10.0.0.5", 80)
    discovery = WLEDDiscovery()

    info_json = {"ver": "0.13.1", "name": "Kitchen", "mac": "11:22:33", "product": "WLED"}
    state_json = {"seg": [{"stop": 60}]}

    with patch("notify.wled_discovery.requests.get") as mock_get:
        mock_get.side_effect = [_ok_json_response(info_json), _ok_json_response(state_json)]
        discovery._get_device_info(device)

    assert device.online is True
    assert device.version == "0.13.1"
    assert device.name == "Kitchen"
    assert device.mac == "11:22:33"
    assert device.led_count == 60


def test_get_device_info_len_segment_fallback():
    device = WLEDDeviceInfo("x", "10.0.0.5", 80)
    discovery = WLEDDiscovery()

    info_json = {"ver": "0.13.1", "name": "Kitchen"}
    state_json = {"seg": [{"len": 42}]}

    with patch("notify.wled_discovery.requests.get") as mock_get:
        mock_get.side_effect = [_ok_json_response(info_json), _ok_json_response(state_json)]
        discovery._get_device_info(device)

    assert device.led_count == 42


def test_get_device_info_leds_key_fallback_when_no_segments():
    device = WLEDDeviceInfo("x", "10.0.0.5", 80)
    discovery = WLEDDiscovery()

    info_json = {"ver": "0.13.1", "name": "Kitchen", "leds": {"count": 100}}
    state_json = {}  # no "seg" key

    with patch("notify.wled_discovery.requests.get") as mock_get:
        mock_get.side_effect = [_ok_json_response(info_json), _ok_json_response(state_json)]
        discovery._get_device_info(device)

    assert device.led_count == 100
    assert device.online is True


def test_get_device_info_segment_without_stop_or_len_leaves_led_count_zero():
    device = WLEDDeviceInfo("x", "10.0.0.5", 80)
    discovery = WLEDDiscovery()

    info_json = {"ver": "0.13.1", "name": "Kitchen"}
    state_json = {"seg": [{"unrelated": True}]}  # neither "stop" nor "len"

    with patch("notify.wled_discovery.requests.get") as mock_get:
        mock_get.side_effect = [_ok_json_response(info_json), _ok_json_response(state_json)]
        discovery._get_device_info(device)

    assert device.online is True
    assert device.led_count == 0


def test_get_device_info_state_fetch_failure_leaves_led_count_zero():
    device = WLEDDeviceInfo("x", "10.0.0.5", 80)
    discovery = WLEDDiscovery()

    info_json = {"ver": "0.13.1", "name": "Kitchen"}

    with patch("notify.wled_discovery.requests.get") as mock_get:
        mock_get.side_effect = [_ok_json_response(info_json), _bad_status_response(500)]
        discovery._get_device_info(device)

    assert device.online is True  # info fetch alone is enough
    assert device.led_count == 0


def test_get_device_info_non_200_status_leaves_device_offline():
    device = WLEDDeviceInfo("x", "10.0.0.5", 80)
    discovery = WLEDDiscovery()

    with patch("notify.wled_discovery.requests.get") as mock_get:
        mock_get.return_value = _bad_status_response(404)
        discovery._get_device_info(device)

    assert device.online is False


def test_get_device_info_missing_ver_and_name_leaves_offline():
    device = WLEDDeviceInfo("x", "10.0.0.5", 80)
    discovery = WLEDDiscovery()

    with patch("notify.wled_discovery.requests.get") as mock_get:
        mock_get.return_value = _ok_json_response({"unrelated": "field"})
        discovery._get_device_info(device)

    assert device.online is False


def test_get_device_info_exception_falls_back_to_plain_get_success():
    device = WLEDDeviceInfo("x", "10.0.0.5", 80)
    discovery = WLEDDiscovery()

    with patch("notify.wled_discovery.requests.get") as mock_get:
        mock_get.side_effect = [ConnectionError("no route"), _bad_status_response(200)]
        discovery._get_device_info(device)

    assert device.online is True
    assert device.product == "WLED (Unknown Version)"
    assert device.led_count == 0


def test_get_device_info_exception_and_fallback_also_fails_stays_offline():
    device = WLEDDeviceInfo("x", "10.0.0.5", 80)
    discovery = WLEDDiscovery()

    with patch("notify.wled_discovery.requests.get") as mock_get:
        mock_get.side_effect = [ConnectionError("no route"), ConnectionError("still no route")]
        discovery._get_device_info(device)

    assert device.online is False


def test_get_device_info_exception_fallback_non_200_stays_offline():
    """Covers the fallback GET's non-200 branch: the exception path's plain
    GET itself resolves (no exception) but with a non-200 status, so the
    device stays offline and no placeholder data is set."""
    device = WLEDDeviceInfo("x", "10.0.0.5", 80)
    discovery = WLEDDiscovery()

    with patch("notify.wled_discovery.requests.get") as mock_get:
        mock_get.side_effect = [ConnectionError("no route"), _bad_status_response(503)]
        discovery._get_device_info(device)

    assert device.online is False
    assert device.product == ""


def test_get_device_info_leds_not_a_dict_preserves_parsed_data_fixed():
    """FIXED LATENT BUG #3: info_data["leds"] is expected to be a dict
    (info_data["leds"].get("count", 0)) but here it's an int. That raises
    AttributeError *after* device.version/name/mac/product were already
    correctly assigned from the same response. The outer except's HTTP
    fallback used to unconditionally overwrite the already-correct
    device.product with the placeholder "WLED (Unknown Version)" and reset
    led_count to 0. Now the fallback only fills in product if it's still
    unset, and never touches led_count -- so data already parsed
    successfully survives the later crash."""
    device = WLEDDeviceInfo("x", "10.0.0.5", 80)
    discovery = WLEDDiscovery()

    info_json = {
        "ver": "0.14.0",
        "name": "Kitchen",
        "mac": "aa:bb:cc",
        "product": "WLED Pro",
        "leds": 5,  # malformed: not a dict, .get() will blow up
    }
    state_json = {}  # no "seg" -> falls into the "leds" fallback branch

    with patch("notify.wled_discovery.requests.get") as mock_get:
        mock_get.side_effect = [
            _ok_json_response(info_json),
            _ok_json_response(state_json),
            _bad_status_response(200),  # the except-handler's fallback plain GET
        ]
        discovery._get_device_info(device)

    # version/mac/product were parsed correctly before the crash and are all
    # preserved -- the fallback no longer clobbers them.
    assert device.version == "0.14.0"
    assert device.mac == "aa:bb:cc"
    assert device.online is True
    assert device.product == "WLED Pro"
    assert device.led_count == 0  # never got a chance to be set differently


# ---------------------------------------------------------------------------
# discover_mdns_devices
# ---------------------------------------------------------------------------


def test_discover_mdns_devices_runs_browser_and_returns_copy():
    with (
        patch("notify.wled_discovery.ServiceBrowser") as mock_browser_cls,
        patch("notify.wled_discovery.time.sleep") as mock_sleep,
    ):
        mock_browser = MagicMock()
        mock_browser_cls.return_value = mock_browser

        discovery = WLEDDiscovery(discovery_timeout=7)
        discovery.zeroconf = MagicMock()
        discovery.discovered_devices = [WLEDDeviceInfo("d1", "1.2.3.4")]

        result = discovery.discover_mdns_devices()

    mock_browser_cls.assert_called_once_with(discovery.zeroconf, "_http._tcp.local.", discovery)
    mock_sleep.assert_called_once_with(7)
    mock_browser.cancel.assert_called_once()
    assert result == discovery.discovered_devices
    assert result is not discovery.discovered_devices  # .copy(), not the same list


def test_discover_mdns_devices_swallows_cancel_error():
    with patch("notify.wled_discovery.ServiceBrowser") as mock_browser_cls, patch("notify.wled_discovery.time.sleep"):
        mock_browser = MagicMock()
        mock_browser.cancel.side_effect = RuntimeError("cancel boom")
        mock_browser_cls.return_value = mock_browser

        discovery = WLEDDiscovery(discovery_timeout=1)
        discovery.zeroconf = MagicMock()

        result = discovery.discover_mdns_devices()  # must not raise

    assert result == []


# ---------------------------------------------------------------------------
# discover_network_scan -- dead code (unreachable from discover_all), but
# still real, testable logic.
# ---------------------------------------------------------------------------


def test_discover_mdns_devices_when_browser_is_falsy_skips_cancel():
    """If ServiceBrowser(...) somehow returns a falsy value, the `if
    self.browser:` guard before .cancel() must skip calling it (rather than
    raising AttributeError on None)."""
    with patch("notify.wled_discovery.ServiceBrowser", return_value=None), patch("notify.wled_discovery.time.sleep"):
        discovery = WLEDDiscovery(discovery_timeout=1)
        discovery.zeroconf = MagicMock()

        result = discovery.discover_mdns_devices()  # must not raise

    assert result == []


def test_discover_network_scan_honors_network_range_parameter_fixed():
    """FIXED LATENT BUG #4: network_range used to be accepted but never
    used -- base_ip was hardcoded to "192.168.1.". Now the first three
    octets of network_range are used as base_ip. Prove it by passing a
    different range and observing the scan probes that range instead."""
    discovery = WLEDDiscovery()
    probed_ips = []

    def fake_get_device_info(self, device):
        probed_ips.append(device.ip)
        if device.ip == "10.0.0.5":
            device.online = True

    with (
        patch("notify.wled_discovery.threading.Thread", ImmediateThread),
        patch.object(WLEDDiscovery, "_get_device_info", fake_get_device_info),
    ):
        devices = discovery.discover_network_scan(network_range="10.0.0.0/24")

    assert all(ip.startswith("10.0.0.") for ip in probed_ips)
    assert len(probed_ips) == 254
    assert len(devices) == 1
    assert devices[0].ip == "10.0.0.5"


def test_discover_network_scan_default_range_still_scans_192_168_1():
    """The default network_range ("192.168.1.0/24") still scans
    192.168.1.* when the caller doesn't override it."""
    discovery = WLEDDiscovery()
    probed_ips = []

    def fake_get_device_info(self, device):
        probed_ips.append(device.ip)

    with (
        patch("notify.wled_discovery.threading.Thread", ImmediateThread),
        patch.object(WLEDDiscovery, "_get_device_info", fake_get_device_info),
    ):
        discovery.discover_network_scan()

    assert all(ip.startswith("192.168.1.") for ip in probed_ips)
    assert len(probed_ips) == 254


def test_discover_network_scan_malformed_range_falls_back_to_default():
    """A network_range that doesn't look like a dotted-quad (fewer than 3
    octets) falls back to the 192.168.1. default rather than crashing."""
    discovery = WLEDDiscovery()
    probed_ips = []

    def fake_get_device_info(self, device):
        probed_ips.append(device.ip)

    with (
        patch("notify.wled_discovery.threading.Thread", ImmediateThread),
        patch.object(WLEDDiscovery, "_get_device_info", fake_get_device_info),
    ):
        discovery.discover_network_scan(network_range="not-an-ip")

    assert all(ip.startswith("192.168.1.") for ip in probed_ips)
    assert len(probed_ips) == 254


def test_discover_network_scan_check_ip_exception_is_swallowed():
    discovery = WLEDDiscovery()

    def raising_get_device_info(self, device):
        raise RuntimeError("boom")

    with (
        patch("notify.wled_discovery.threading.Thread", ImmediateThread),
        patch.object(WLEDDiscovery, "_get_device_info", raising_get_device_info),
    ):
        devices = discovery.discover_network_scan()  # must not raise

    assert devices == []


# ---------------------------------------------------------------------------
# discover_all
# ---------------------------------------------------------------------------


def test_discover_all_dedupes_by_ip():
    discovery = WLEDDiscovery()
    d1 = WLEDDeviceInfo("first", "10.0.0.5")
    d2_dupe_ip = WLEDDeviceInfo("second-same-ip", "10.0.0.5")
    d3 = WLEDDeviceInfo("third", "10.0.0.6")

    with (
        patch.object(discovery, "discover_mdns_devices", return_value=[d1, d2_dupe_ip, d3]),
        patch.object(discovery, "discover_network_scan") as mock_scan,
    ):
        result = discovery.discover_all()

    assert result == [d1, d3]
    mock_scan.assert_not_called()  # network scan is disabled by default (commented out)


def test_discover_all_swallows_mdns_exception():
    discovery = WLEDDiscovery()
    with patch.object(discovery, "discover_mdns_devices", side_effect=RuntimeError("mdns boom")):
        result = discovery.discover_all()

    assert result == []


# ---------------------------------------------------------------------------
# discover_wled_devices -- the public, route-facing entry point
# ---------------------------------------------------------------------------


def test_discover_wled_devices_returns_dicts():
    device = WLEDDeviceInfo("Patio", "192.168.1.9", 80)
    device.online = True
    device.version = "0.14.0"

    with patch("notify.wled_discovery.Zeroconf"), patch.object(WLEDDiscovery, "discover_all", return_value=[device]):
        result = discover_wled_devices(timeout=5)

    assert result == [device.to_dict()]
    assert result[0]["name"] == "Patio"


def test_discover_wled_devices_empty_when_no_devices_found():
    with patch("notify.wled_discovery.Zeroconf"), patch.object(WLEDDiscovery, "discover_all", return_value=[]):
        result = discover_wled_devices(timeout=5)

    assert result == []


def test_discover_wled_devices_returns_empty_list_on_exception():
    with patch("notify.wled_discovery.Zeroconf", side_effect=RuntimeError("zeroconf init failed")):
        result = discover_wled_devices(timeout=5)

    assert result == []

from common.common import default_settings, display_sleep_timeout


def test_default_settings_has_sleep_timeout():
	assert default_settings()['display']['sleep_timeout'] == 300


def test_accessor_reads_value():
	assert display_sleep_timeout({'display': {'sleep_timeout': 45}}) == 45


def test_accessor_zero_means_never():
	assert display_sleep_timeout({'display': {'sleep_timeout': 0}}) == 0


def test_accessor_missing_defaults_to_300():
	assert display_sleep_timeout({'display': {}}) == 300
	assert display_sleep_timeout({}) == 300


def test_accessor_negative_clamps_to_zero():
	assert display_sleep_timeout({'display': {'sleep_timeout': -5}}) == 0


def test_accessor_non_numeric_defaults_to_300():
	assert display_sleep_timeout({'display': {'sleep_timeout': 'x'}}) == 300

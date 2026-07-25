#!/usr/bin/env python3

# *****************************************
# PiFire hcsr04 Interface Library
# *****************************************
#
# Description: This library supports getting
# 	the hopper level from the distance sensor
#  NOTE: This library hasn't been tested with real
#  hardware yet and is provided for testing.
#
# Library dependency installation instructions:
#  sudo pip3 install hcsr04sensor
#
# *****************************************

# *****************************************
# Imported Libraries
# *****************************************

from hcsr04sensor import sensor

from distance._sampled_base import SampledHopperLevel


class HopperLevel(SampledHopperLevel):
    """Ultrasonic hopper level, sampled on a background thread.

    Shares distance/_sampled_base.py's sampling loop with the ToF sensors so
    that get_level() is a free cached read for the control loop's timed
    refresh, instead of a synchronous ultrasonic measurement on the loop's
    own thread.
    """

    sensor_label = "HC-SR04 sensor"

    # hcsr04sensor's raw_distance() already averages a burst of pings
    # internally (11 by default, spaced by its own sample_wait), so one call
    # per cycle IS the average. Taking the base's default 3 would triple a
    # read that is already ~1.1s of deliberate sleeps, for no extra accuracy.
    samples_per_cycle = 1

    # Consequently a HEALTHY ultrasonic read is ~1.1s, where a healthy ToF
    # read is ~0.1-0.2s. Keeping the ToF sensors' 0.5s threshold here would
    # declare every normal reading a stuck sensor and re-initialize forever.
    slow_cycle_seconds = 2.0

    def __init__(self, dev_pins, empty=22, full=4, debug=False):
        super().__init__(empty=empty, full=full, debug=debug)

        # (NOTE: This is a 5V device and must be connected to 5V VCC)
        self.trig_pin = dev_pins["distance"]["trig"]
        # (NOTE: This pin (echo_pin) must have a resistor divider to reduce the voltage to tolerable levels)
        # (Details: https://www.linuxnorth.org/hcsr04sensor/)
        self.echo_pin = dev_pins["distance"]["echo"]

        # Default values
        # unit = 'metric'
        # temperature = 20 (room temp in Celsius)

        self._restart_sensor()
        # Setup & Start Sensor Loop Thread
        self._start_sampling()

    def _restart_sensor(self):
        #  Create a distance reading with the hcsr04 sensor module
        self.ultrasonic = sensor.Measurement(self.trig_pin, self.echo_pin)

    def _read_distance_mm(self):
        # raw_distance() answers in cm; the sampling loop works in mm.
        return self.ultrasonic.raw_distance() * 10

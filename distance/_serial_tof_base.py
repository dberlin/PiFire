#!/usr/bin/env python3

# *****************************************
# PiFire Serial ToF (Time-of-Flight) Hopper Level Base
# *****************************************
#
# Description: Serial-port opening and sensor open/re-open logic for
#   USB-serial-connected Time-of-Flight distance sensors (e.g. the DFRobot
#   SEN0628). The sampling thread, percentage math and cached get_level()
#   contract live in distance/_sampled_base.py, shared with the I2C ToF and
#   ultrasonic transports. Each sensor module subclasses
#   SerialToFHopperLevel and implements _open_sensor, _read_distance_mm, and
#   (optionally) _close_sensor.
#
#   Re-init here is tolerant of failure -- unlike the I2C bases, a serial
#   device that has been unplugged raises on re-open, and that must not take
#   down the sampling thread.
#
# *****************************************

import serial

from distance._sampled_base import SampledHopperLevel


class SerialToFHopperLevel(SampledHopperLevel):
    default_device = "/dev/ttyACM0"
    default_baudrate = 115200

    sensor_label = "serial ToF sensor"

    def __init__(self, dev_pins, empty=22, full=4, debug=False):
        super().__init__(empty=empty, full=full, debug=debug)

        distance_pins = (dev_pins or {}).get("distance", {}) or {}
        self.device = distance_pins.get("device", self.default_device)
        self.baudrate = distance_pins.get("baudrate", self.default_baudrate)

        self.__start_sensor()
        # Setup & Start Sensor Loop Thread
        self._start_sampling()

    def _open_serial_port(self):
        return serial.Serial(self.device, self.baudrate, timeout=0.2)

    def __start_sensor(self):
        self._close_sensor()
        ser = self._open_serial_port()
        self._serial_port = ser
        self._open_sensor(ser)

    def _restart_sensor(self):
        try:
            self.__start_sensor()
        except Exception:
            self.logger.exception("Serial ToF sensor re-init failed; will retry on the next slow read cycle.")

    def _open_sensor(self, ser):
        """Initialize the sensor protocol on the already-open `ser` (a
        pyserial Serial instance) and set whatever state _read_distance_mm
        needs (e.g. self.ser). Subclasses must implement this."""
        raise NotImplementedError

    def _read_distance_mm(self):
        """Return a single distance reading in millimeters. Subclasses must
        implement this."""
        raise NotImplementedError

    def _close_sensor(self):
        """Close the serial port opened by __start_sensor, if any. Subclasses
        that need additional teardown should call super()._close_sensor()."""
        ser = getattr(self, "_serial_port", None)
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass
            self._serial_port = None

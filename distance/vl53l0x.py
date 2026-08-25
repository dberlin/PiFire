# *****************************************
# PiFire vl53l0x Interface Library
# *****************************************
#
# Description: This library supports getting the hopper level from the
#   VL53L0X distance sensor, via Adafruit's CircuitPython library.
#
# *****************************************

from adafruit_vl53l0x import VL53L0X

from distance._tof_base import ToFHopperLevel


class HopperLevel(ToFHopperLevel):
    default_address = 0x29

    def _open_sensor(self, i2c, address):
        # This driver polls the sensor's status registers itself, and every one
        # of those loops is bounded only when io_timeout_s is greater than
        # zero -- which is not its default. Left unset, a VL53L0X that stops
        # answering holds the shared bus exactly as an unbounded poll of our
        # own would. The driver applies the value per poll rather than per
        # reading, so `.range` gives up after a small multiple of it; that is
        # still bounded, and still an order of magnitude inside the watchdog's
        # stuck_cycle_seconds.
        self.tof = VL53L0X(i2c, address=address, io_timeout_s=self.read_deadline_seconds)

    def _read_distance_mm(self):
        return self.tof.range

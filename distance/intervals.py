#!/usr/bin/env python3

# *****************************************
# PiFire Hopper Level Timing Constants
# *****************************************
#
# Description: The numbers that decide how fresh the hopper reading on the
#   dashboard is, and how long the controller's boot will wait for the sensor
#   that produces it. They are here, together, in one importable place
#   because they only make sense relative to each other -- changing one
#   without looking at the others is what this module exists to prevent.
#
# *****************************************

# How often a distance sensor's background thread takes a fresh sample and
# updates its cached hopper level.
#
# The sampling threads (distance/_sampled_base.py) sleep 1s between checks,
# so the real sampling period is this value plus up to a second. Every
# supported sensor's sample cycle is cheap relative to this: the ToF sensors
# average 3 reads at a 33-50ms timing budget each (~0.1-0.2s, which is why
# their stuck-sensor threshold is 0.5s), and the ultrasonic HC-SR04 takes one
# ~1.1s `raw_distance()` burst. So the cost is a few percent of one
# background thread, and never a cost on the control loop.
SENSOR_SAMPLE_INTERVAL = 8

# How often the control loop copies a sensor's already-cached level into
# pelletdb, which is what the dashboard renders.
#
# Deliberately SLOWER than SENSOR_SAMPLE_INTERVAL, so that every poll finds a
# sample the sampling thread has already taken. The read itself is
# `get_level(override=False)`: it returns the cached value immediately.
# Polling with override=True would block the control loop for up to 3s per
# poll (see get_level below) while it timed the auger and igniter -- roughly a
# third of the period, which is why the automatic refresh must never use it.
HOPPER_LEVEL_REFRESH_INTERVAL = 10

# How long a distance sensor gets to finish opening before the controller
# gives up on it, falls back to distance.none and carries on.
#
# The open runs on control.py's boot path, so every second here is a second
# with no control loop and no API. The value sits between the two numbers that
# bracket it. Below it: a healthy open is far quicker than a healthy sample
# cycle, and the slowest of those is the ultrasonic HC-SR04's ~1.1s
# `raw_distance()` burst (which is why its slow_cycle_seconds is 2.0, against
# 0.5 for the ToF sensors), so this leaves several times the room a working
# sensor needs even on a cold USB-serial port. Above it: less than one
# SENSOR_SAMPLE_INTERVAL, so a sensor that never opens costs the boot less
# than the period at which a working one would have been sampled anyway.
SENSOR_OPEN_DEADLINE = 5

# How long one ToF reading may wait for the sensor to report data ready before
# the read fails instead of waiting longer.
#
# The wait is a poll of an I2C register, on a bus every device sharing that
# hardware also uses (common/i2c_bus.py hands them all the same cached bus and
# its one lock), so a sensor that never answers costs the probes and the grill
# platform their bus, not just the hopper reading. Ten times the 50ms timing
# budget both Adafruit ToF drivers configure, which is the interval a healthy
# part actually needs to produce a reading. It equals the ToF sensors'
# slow_cycle_seconds, so a read that runs this long has already made its cycle
# a slow one, and it is far below stuck_cycle_seconds (30), which keeps the
# watchdog the outer net rather than the first thing to notice.
TOF_READ_DEADLINE = 0.5

# How often that wait re-reads the data-ready register.
#
# Each check is one transaction on the shared bus. The parts need ~50ms to
# produce a reading, so asking faster only repeats a question that cannot have
# a new answer yet: at 10ms a healthy reading costs about five transactions and
# arrives at most 10ms late, where 1ms cost about fifty for the same reading,
# and a failing sensor spent the whole deadline at ~1000 bus acquisitions a
# second.
TOF_DATA_READY_POLL = 0.01

# The wait a sampler takes before retrying after one failed cycle, and the
# longest wait a run of them can reach. Delays double from the base -- 1, 2, 4,
# 8, then the cap -- and the first cycle that succeeds returns to the base.
#
# The base is the sampling loop's own 1s pacing tick: anything smaller would be
# rounded up to it anyway. The cap is the owner's number. It reads oddly beside
# SENSOR_SAMPLE_INTERVAL, which is already 8, and that is because widening the
# gap between timed cycles is not what the backoff is mainly for:
# `request_sample()` can pull the next cycle forward to the next tick, and both
# controller/runtime/controller.py and controller/runtime/modes/base.py call
# it, so a failing sensor can be asked ~once a second without one. The backoff
# is a floor under the interval, not a replacement for it -- the interval still
# governs the healthy path, where there are no consecutive failures to count.
SENSOR_BACKOFF_BASE = 1
SENSOR_BACKOFF_CAP = 10

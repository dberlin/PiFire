#!/usr/bin/env python3

# *****************************************
# PiFire Hopper Level Timing Constants
# *****************************************
#
# Description: The two numbers that decide how fresh the hopper reading on
#   the dashboard is. They are here, together, in one importable place
#   because they only make sense relative to each other -- changing one
#   without looking at the other is what this module exists to prevent.
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

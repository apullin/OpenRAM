#!/usr/bin/env python3
# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.

import importlib
import os
import sys
import unittest

from testutils import debugTestRunner, header, openram_test

import openram
from openram import OPTS


class delay_period_state_test(openram_test):
    """Keep downstream characterization state coherent and conservative."""

    def setUp(self):
        super().setUp()
        config_file = "{}/tests/configs/config".format(os.getenv("OPENRAM_HOME"))
        openram.init_openram(config_file, is_unit_test=True)
        delay_module = importlib.import_module("openram.characterizer.delay")
        self.delay_class = delay_module.delay

    def tearDown(self):
        openram.end_openram()
        super().tearDown()

    def test_leakage_uses_accepted_minimum_period(self):
        instance = self.delay_class.__new__(self.delay_class)
        accepted_period = 4.0625
        residual_probe_period = 3.90625
        observed = {}

        instance.analysis_init = lambda probe_address, probe_data: None
        instance.find_feasible_period = lambda: {}
        instance.set_load_slew = lambda load, slew: None

        def find_min_period(feasible_delays):
            instance.period = residual_probe_period
            return accepted_period

        def run_power_simulation():
            observed["leakage_period"] = instance.period
            return (1.0, 0.5)

        instance.find_min_period = find_min_period
        instance.run_power_simulation = run_power_simulation
        instance.simulate_loads_and_slews = lambda load_slews, leakage_offset: {}
        instance.sen_path_meas = []
        instance.bl_path_meas = []
        instance.alter_lh_char_data = lambda char_port_data: None

        instance.analyze("0", 0, [(1.0, 0.01)])

        self.assertEqual(observed["leakage_period"], accepted_period)
        self.assertEqual(instance.period, accepted_period)

    def test_rise_fall_proxy_is_elementwise_conservative(self):
        instance = self.delay_class.__new__(self.delay_class)
        instance.all_ports = [0]
        char_port_data = {
            0: {
                "delay_lh": [2.0, 1.0],
                "delay_hl": [1.0, 3.0],
                "slew_lh": [0.8, 0.1],
                "slew_hl": [0.07, 0.2],
            }
        }

        instance.alter_lh_char_data(char_port_data)

        self.assertEqual(char_port_data[0]["delay_lh"], [2.0, 3.0])
        self.assertEqual(char_port_data[0]["delay_hl"], [2.0, 3.0])
        self.assertEqual(char_port_data[0]["slew_lh"], [0.8, 0.2])
        self.assertEqual(char_port_data[0]["slew_hl"], [0.8, 0.2])

    def test_rise_fall_proxy_handles_empty_write_only_port(self):
        instance = self.delay_class.__new__(self.delay_class)
        instance.all_ports = [0]
        char_port_data = {
            0: {
                "delay_lh": [],
                "delay_hl": [],
                "slew_lh": [],
                "slew_hl": [],
            }
        }

        instance.alter_lh_char_data(char_port_data)

        self.assertEqual(char_port_data[0]["delay_lh"], [])
        self.assertEqual(char_port_data[0]["delay_hl"], [])
        self.assertEqual(char_port_data[0]["slew_lh"], [])
        self.assertEqual(char_port_data[0]["slew_hl"], [])

    def test_rise_fall_proxy_rejects_mismatched_lengths(self):
        instance = self.delay_class.__new__(self.delay_class)
        instance.all_ports = [0]
        char_port_data = {
            0: {
                "delay_lh": [1.0],
                "delay_hl": [],
                "slew_lh": [],
                "slew_hl": [],
            }
        }

        with self.assertRaises(AssertionError):
            instance.alter_lh_char_data(char_port_data)

    def test_rise_fall_proxy_keeps_ports_and_directions_independent(self):
        instance = self.delay_class.__new__(self.delay_class)
        instance.all_ports = [0, 1]
        char_port_data = {
            0: {
                "delay_lh": [2.0],
                "delay_hl": [1.0],
                "slew_lh": [0.8],
                "slew_hl": [0.1],
            },
            1: {
                "delay_lh": [3.0],
                "delay_hl": [4.0],
                "slew_lh": [0.2],
                "slew_hl": [0.3],
            },
        }

        instance.alter_lh_char_data(char_port_data)

        self.assertEqual(char_port_data[0]["delay_lh"], [2.0])
        self.assertEqual(char_port_data[1]["delay_lh"], [4.0])
        self.assertIsNot(char_port_data[0]["delay_lh"],
                         char_port_data[0]["delay_hl"])
        self.assertIsNot(char_port_data[0]["delay_lh"],
                         char_port_data[1]["delay_lh"])
        char_port_data[0]["delay_lh"][0] = 99.0
        self.assertEqual(char_port_data[0]["delay_hl"], [2.0])
        self.assertEqual(char_port_data[1]["delay_lh"], [4.0])

    def test_precharge_delay_substitution_keeps_conservative_slew(self):
        instance = self.delay_class.__new__(self.delay_class)
        instance.period = 4.0625
        instance.load = 1.7225
        instance.slew = 0.04
        instance.all_ports = [0]
        raw_result = {
            "delay_lh": 1.380553,
            "delay_hl": -1.020226,
            "slew_lh": 0.8025825,
            "slew_hl": 0.07129093,
        }

        self.assertTrue(instance.check_valid_delays(raw_result))
        char_port_data = {
            0: {name: [value] for name, value in raw_result.items()}
        }
        instance.alter_lh_char_data(char_port_data)

        self.assertEqual(char_port_data[0]["delay_lh"], [1.380553])
        self.assertEqual(char_port_data[0]["delay_hl"], [1.380553])
        self.assertEqual(char_port_data[0]["slew_lh"], [0.8025825])
        self.assertEqual(char_port_data[0]["slew_hl"], [0.8025825])

    def test_non_finite_delay_measurements_are_rejected(self):
        instance = self.delay_class.__new__(self.delay_class)
        instance.period = 4.0625
        instance.load = 1.7225
        instance.slew = 0.04

        for bad_value in (float("nan"), float("inf"), float("-inf")):
            result = {
                "delay_lh": 1.0,
                "delay_hl": 1.0,
                "slew_lh": bad_value,
                "slew_hl": 0.5,
            }
            with self.subTest(bad_value=bad_value):
                self.assertFalse(instance.check_valid_delays(result))


if __name__ == "__main__":
    (OPTS, args) = openram.parse_args()
    del sys.argv[1:]
    header(__file__, OPTS.tech_name)
    unittest.main(testRunner=debugTestRunner())

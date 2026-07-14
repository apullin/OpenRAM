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
import types
import unittest

from testutils import debugTestRunner, header, openram_test

import openram
from openram import OPTS


class lib_corner_lifecycle_test(openram_test):
    """Keep corner selection and per-corner state deterministic and isolated."""

    def setUp(self):
        super().setUp()
        config_file = "{}/tests/configs/config".format(os.getenv("OPENRAM_HOME"))
        openram.init_openram(config_file, is_unit_test=True)
        self.lib_module = importlib.import_module("openram.characterizer.lib")
        self.lib_class = self.lib_module.lib

    def tearDown(self):
        openram.end_openram()
        super().tearDown()

    def bare_lib(self):
        instance = self.lib_class.__new__(self.lib_class)
        instance.out_dir = OPTS.openram_temp
        instance.sram = types.SimpleNamespace(name="corner_lifecycle")
        return instance

    def test_only_config_corners_are_exact_ordered_product(self):
        """Config-only mode must not require or inject a nominal corner."""
        OPTS.use_specified_corners = None
        OPTS.only_use_config_corners = True
        OPTS.nominal_corner_only = True
        OPTS.process_corners = ["SS", "FF"]
        OPTS.supply_voltages = [0.9, 1.1]
        OPTS.temperatures = [-40, 125]

        instance = self.bare_lib()
        instance.create_corners()

        expected = [(process, voltage, temperature)
                    for process in OPTS.process_corners
                    for voltage in OPTS.supply_voltages
                    for temperature in OPTS.temperatures]
        self.assertEqual(instance.corners, expected)
        self.assertNotIn(("TT",
                          self.lib_module.tech.spice["nom_supply_voltage"],
                          self.lib_module.tech.spice["nom_temperature"]),
                         instance.corners)

    def test_generated_corner_order_is_stable_and_unique(self):
        """Generated corners have a documented order independent of set hashes."""
        nominal_voltage = self.lib_module.tech.spice["nom_supply_voltage"]
        nominal_temperature = self.lib_module.tech.spice["nom_temperature"]
        low_voltage = nominal_voltage - 0.1
        high_voltage = nominal_voltage + 0.1
        OPTS.use_specified_corners = None
        OPTS.only_use_config_corners = False
        OPTS.nominal_corner_only = False
        OPTS.process_corners = ["TT", "FF", "SS"]
        OPTS.supply_voltages = [nominal_voltage, low_voltage, high_voltage]
        OPTS.temperatures = [nominal_temperature, -40, 125]

        instance = self.bare_lib()
        instance.create_corners()

        expected = [
            ("TT", nominal_voltage, nominal_temperature),
            ("TT", nominal_voltage, -40),
            ("TT", nominal_voltage, 125),
            ("TT", low_voltage, nominal_temperature),
            ("TT", high_voltage, nominal_temperature),
            ("FF", nominal_voltage, nominal_temperature),
            ("SS", nominal_voltage, nominal_temperature),
        ]
        self.assertEqual(instance.corners, expected)
        self.assertEqual(len(instance.corners), len(set(instance.corners)))

    def test_setup_hold_is_recomputed_for_each_corner(self):
        """One lib object must not reuse first-corner constraint matrices."""
        calls = []

        class fake_setup_hold:
            def __init__(self, corner):
                self.corner = corner
                calls.append(corner)

            def analyze(self, related_slews, constrained_slews):
                return {"corner": self.corner,
                        "related_slews": tuple(related_slews),
                        "constrained_slews": tuple(constrained_slews)}

        instance = self.bare_lib()
        instance.use_model = False
        instance.slews = [0.01, 0.02]
        tt_corner = ("TT", 1.0, 25)
        ss_corner = ("SS", 0.9, 125)
        original_setup_hold = self.lib_module.setup_hold
        self.lib_module.setup_hold = fake_setup_hold
        try:
            instance.corner = tt_corner
            instance.compute_setup_hold()
            tt_times = instance.times

            instance.corner = ss_corner
            instance.compute_setup_hold()
            ss_times = instance.times
        finally:
            self.lib_module.setup_hold = original_setup_hold

        self.assertEqual(calls, [tt_corner, ss_corner])
        self.assertEqual(tt_times["corner"], tt_corner)
        self.assertEqual(ss_times["corner"], ss_corner)
        self.assertEqual(instance.sh.corner, ss_corner)


if __name__ == "__main__":
    (OPTS, args) = openram.parse_args()
    del sys.argv[1:]
    header(__file__, OPTS.tech_name)
    unittest.main(testRunner=debugTestRunner())

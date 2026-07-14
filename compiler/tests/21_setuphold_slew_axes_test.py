#!/usr/bin/env python3
# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.

import os
import re
import sys
import types
import unittest
from importlib import reload

from testutils import debugTestRunner, header, openram_test

import openram
from openram import OPTS


class setup_hold_slew_axes_test(openram_test):
    """Ensure setup/hold decks preserve both Liberty constraint axes."""

    def setUp(self):
        super().setUp()
        config_file = "{}/tests/configs/config".format(os.getenv("OPENRAM_HOME"))
        openram.init_openram(config_file, is_unit_test=True)
        OPTS.spice_name = "ngspice"
        OPTS.analytical_delay = False
        OPTS.netlist_only = True

        # Reload after selecting the simulator, matching the characterization tests.
        from openram import characterizer
        reload(characterizer)
        from openram.characterizer import setup_hold

        corner = (OPTS.process_corners[0],
                  OPTS.supply_voltages[0],
                  OPTS.temperatures[0])
        self.sh = setup_hold(corner)

    def tearDown(self):
        openram.end_openram()
        super().tearDown()

    @staticmethod
    def waveform_slew(deck, source_name):
        """Return the first PWL transition width for a named voltage source."""
        source = next(line for line in deck.splitlines()
                      if line.startswith("V{} ".format(source_name)))
        times = [float(value)
                 for value in re.findall(r"([-+0-9.eE]+)n\s+[-+0-9.eE]+v", source)]
        if len(times) < 3:
            raise AssertionError("Malformed {} PWL source: {}".format(source_name,
                                                                       source))
        return times[2] - times[1]

    def write_and_measure_deck(self, mode="SETUP", correct_value=1):
        target_time = (1.5 if mode == "SETUP" else 2.5) * self.sh.period
        self.sh.write_stimulus(mode=mode,
                               target_time=target_time,
                               correct_value=correct_value)
        deck_path = os.path.join(OPTS.openram_temp, "sh_stim.sp")
        with open(deck_path, encoding="utf-8") as deck_file:
            deck = deck_file.read()
        return (self.waveform_slew(deck, "clk"),
                self.waveform_slew(deck, "D"))

    def test_unequal_slew_axes_reach_deck(self):
        self.sh.related_input_slew = 0.031
        self.sh.constrained_input_slew = 0.007

        clock_slew, data_slew = self.write_and_measure_deck()

        self.assertAlmostEqual(clock_slew, self.sh.related_input_slew, places=9)
        self.assertAlmostEqual(data_slew, self.sh.constrained_input_slew, places=9)
        self.assertNotEqual(clock_slew, data_slew)

    def test_related_slew_rows_are_distinct(self):
        related_slews = [0.003, 0.011, 0.037]
        constrained_slews = [0.002, 0.007, 0.019]

        def deck_encoded_search(sh, correct_value, mode):
            clock_slew, data_slew = self.write_and_measure_deck(mode,
                                                                 correct_value)
            # A deterministic stand-in for a simulator result which depends on
            # both generated waveforms. It keeps this matrix regression fast.
            return round(1000 * clock_slew + data_slew, 9)

        self.sh.bidir_search = types.MethodType(deck_encoded_search, self.sh)
        matrices = self.sh.analyze(related_slews, constrained_slews)

        expected_rows = [tuple(round(1000 * related + constrained, 9)
                               for constrained in constrained_slews)
                         for related in related_slews]
        for name, values in matrices.items():
            rows = [tuple(values[start:start + len(constrained_slews)])
                    for start in range(0, len(values), len(constrained_slews))]
            self.assertEqual(rows, expected_rows, name)
            self.assertEqual(len(set(rows)), len(related_slews),
                             "{} duplicated related-slew rows".format(name))


if __name__ == "__main__":
    (OPTS, args) = openram.parse_args()
    del sys.argv[1:]
    header(__file__, OPTS.tech_name)
    unittest.main(testRunner=debugTestRunner())

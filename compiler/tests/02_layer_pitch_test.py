#!/usr/bin/env python3
# See LICENSE for licensing information.
#
# Copyright (c) 2016-2026 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.

import os
import sys
import unittest

from common import make_openram_package
make_openram_package()

from testutils import *

import openram
from openram import debug


class layer_pitch_test(openram_test):
    """Verify that a reversed stack uses the via metal on that stack."""

    def runTest(self):
        config_file = "{}/tests/configs/config".format(
            os.getenv("OPENRAM_HOME"))
        openram.init_openram(config_file, is_unit_test=True)

        from openram.base import design
        from openram.base.hierarchy_layout import layout
        from openram.base.utils import round_to_grid
        from openram.tech import m2_stack, preferred_directions

        probe = design("layer_pitch_probe")
        lower_via = layout.m2_via
        if preferred_directions["m3"] == "V":
            via_width = lower_via.second_layer_width
        else:
            via_width = lower_via.second_layer_height
        expected = round_to_grid(via_width + probe.m3_space)

        actual = layout.compute_layer_pitch(m2_stack[::-1], True)
        self.assertAlmostEqual(actual, expected)
        self.assertGreaterEqual(probe.m3_pitch, expected)

        openram.end_openram()


if __name__ == "__main__":
    (OPTS, args) = openram.parse_args()
    del sys.argv[1:]
    header(__file__, OPTS.tech_name)
    unittest.main(testRunner=debugTestRunner())

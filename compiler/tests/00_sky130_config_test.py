#!/usr/bin/env python3
# See LICENSE for licensing information.
#
# Copyright (c) 2016-2026 Regents of the University of California, Santa Cruz
# All rights reserved.
#
import os
import runpy
import sys
import unittest
from testutils import *

import openram
from openram import debug


class sky130_config_test(openram_test):
    """Check that the shipped SKY130 supply-router options have valid types."""

    def runTest(self):
        openram_root = os.path.abspath(os.path.join(os.environ["OPENRAM_HOME"], ".."))
        config_file = os.path.join(openram_root,
                                   "macros",
                                   "sram_configs",
                                   "sky130_sram_1rw_tiny.py")
        config = runpy.run_path(config_file)

        self.assertIs(config["route_supplies"], True)
        self.assertEqual(config["supply_pin_type"], "ring")


if __name__ == "__main__":
    (OPTS, args) = openram.parse_args()
    del sys.argv[1:]
    header(__file__, OPTS.tech_name)
    unittest.main(testRunner=debugTestRunner())

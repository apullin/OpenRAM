#!/usr/bin/env python3
# See LICENSE for licensing information.
#
# Copyright (c) 2016-2026 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.

import io
import os
import sys
import unittest

from common import make_openram_package
make_openram_package()

from testutils import *

import openram
from openram import debug


class verilog_spare_col_test(openram_test):

    def runTest(self):
        config_file = "{}/tests/configs/config".format(
            os.getenv("OPENRAM_HOME"))
        openram.init_openram(config_file, is_unit_test=True)

        from openram.base.verilog import verilog

        model = verilog()
        model.vf = io.StringIO()
        model.word_size = 8
        model.write_size = 8
        model.num_spare_cols = 1
        model.readwrite_ports = [0]
        model.write_ports = [0]

        model.add_write_block(0)
        generated = model.vf.getvalue()

        self.assertIn(
            "mem[addr0_reg][7:0] = din0_reg[7:0];", generated)
        self.assertIn(
            "mem[addr0_reg][8] = din0_reg[8];", generated)
        self.assertNotIn(
            "mem[addr0_reg][6:0] = din0_reg[6:0];", generated)

        openram.end_openram()


if __name__ == "__main__":
    (OPTS, args) = openram.parse_args()
    del sys.argv[1:]
    header(__file__, OPTS.tech_name)
    unittest.main(testRunner=debugTestRunner())

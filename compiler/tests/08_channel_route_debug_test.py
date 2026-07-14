#!/usr/bin/env python3
# See LICENSE for licensing information.
#
# Copyright (c) 2016-2026 Regents of the University of California, Santa Cruz
# All rights reserved.
#
import os
import sys
import unittest
from testutils import *

import openram
from openram import debug, OPTS


class fake_pin():
    def __init__(self, x, y):
        self.x = x
        self.y = y

    def lx(self):
        return self.x - 0.1

    def rx(self):
        return self.x + 0.1

    def by(self):
        return self.y

    def uy(self):
        return self.y + 0.1

    def center(self):
        from openram.base import vector
        return vector(self.x, self.y + 0.05)


class unwritable_parent():
    def __init__(self):
        self.path = None

    def gds_write(self, path):
        self.path = path
        raise PermissionError("read-only current directory")


class channel_route_debug_test(openram_test):
    """A debug-artifact failure must not mask a cyclic-VCG failure."""

    def runTest(self):
        config_file = "{}/tests/configs/config".format(os.getenv("OPENRAM_HOME"))
        openram.init_openram(config_file, is_unit_test=True)
        from openram.base import channel_route, vector

        router = channel_route.__new__(channel_route)
        router.netlist = [
            [fake_pin(0, 0), fake_pin(0, 2)],
            [fake_pin(0, 2), fake_pin(0, 0)],
        ]
        router.offset = vector(0, 0)
        router.vertical = False
        router.vertical_nonpref_pitch = 1
        router.horizontal_nonpref_pitch = 1
        router.parent = unwritable_parent()

        with self.assertRaises(AssertionError):
            router.route()

        expected = os.path.join(OPTS.openram_temp, "vcg.gds")
        self.assertEqual(router.parent.path, expected)
        openram.end_openram()


if __name__ == "__main__":
    (OPTS, args) = openram.parse_args()
    del sys.argv[1:]
    header(__file__, OPTS.tech_name)
    unittest.main(testRunner=debugTestRunner())

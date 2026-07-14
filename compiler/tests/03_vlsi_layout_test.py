#!/usr/bin/env python3
# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California, Santa Cruz
# All rights reserved.
#
import sys
import unittest

from testutils import *

import openram
from openram import debug
from openram import OPTS


class vlsi_layout_test(openram_test):

    def runTest(self):
        from openram.gdsMill import gdsMill

        layout = gdsMill.VlsiLayout()
        layout.getTexts = lambda _lpp: []

        def unexpected_shape_walk(_lpp):
            self.fail("shape hierarchy must not be walked without labels")

        layout.getAllShapes = unexpected_shape_walk
        layout.processLabelPins((1, 0))
        self.assertEqual(layout.pins, {})


if __name__ == "__main__":
    (OPTS, args) = openram.parse_args()
    del sys.argv[1:]
    header(__file__, OPTS.tech_name)
    unittest.main(testRunner=debugTestRunner())

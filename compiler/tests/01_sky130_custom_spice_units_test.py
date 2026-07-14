#!/usr/bin/env python3
# See LICENSE for licensing information.
#
# Copyright (c) 2016-2026 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.
#
import re
import sys
import unittest
from pathlib import Path


class sky130_custom_spice_units_test(unittest.TestCase):
    """Check that custom-cell device dimensions have explicit SI scaling."""

    custom_cells = (
        "sky130_custom_cell.sp",
        "sky130_custom_dummy.sp",
        "sky130_custom_nand2_dec.sp",
        "sky130_custom_replica.sp",
    )
    dimensional_properties = {"ad", "as", "pd", "ps", "w", "l"}
    bare_decimal = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")

    def test_custom_device_dimensions_are_scaled(self):
        repo_root = Path(__file__).resolve().parents[2]
        spice_dir = repo_root / "technology" / "sky130" / "sp_lib"
        checked_properties = 0
        for filename in self.custom_cells:
            path = spice_dir / filename
            self.assertTrue(path.is_file(), "Missing {}".format(path))
            with open(path, encoding="utf-8") as spice_file:
                for line_number, line in enumerate(spice_file, 1):
                    if not line.startswith("X"):
                        continue
                    for token in line.split():
                        if "=" not in token:
                            continue
                        name, value = token.split("=", 1)
                        if name.lower() not in self.dimensional_properties:
                            continue
                        checked_properties += 1
                        self.assertIsNone(
                            self.bare_decimal.fullmatch(value),
                            "{}:{} has an unscaled {}={}".format(
                                filename, line_number, name, value
                            ),
                        )

        self.assertGreater(checked_properties, 0)


if __name__ == "__main__":
    # The OpenRAM Makefile supplies technology-selection options to every
    # test. This source-library check is always SKY130-specific and needs none.
    sys.argv[1:] = []
    unittest.main()

#!/usr/bin/env python3
# See LICENSE for licensing information.
#
# Copyright (c) 2016-2026 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.
#
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import openram
from openram import OPTS
from testutils import debugTestRunner, header, openram_test


class sky130_custom_spice_views_test(openram_test):
    """Exercise the distinct SKY130 simulation and Netgen LVS cell views."""

    custom_cells = (
        "sky130_custom_cell",
        "sky130_custom_dummy",
        "sky130_custom_nand2_dec",
        "sky130_custom_nand3_dec",
        "sky130_custom_nand4_dec",
        "sky130_custom_replica",
    )
    dimensional_properties = {"ad", "as", "pd", "ps", "w", "l"}
    bare_decimal = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")

    @staticmethod
    def subckt_pins(lines, cell_name):
        prefix = ".subckt {} ".format(cell_name).lower()
        for line in lines:
            if line.lower().startswith(prefix):
                return line.split()[2:]
        raise AssertionError("Missing .subckt {}".format(cell_name))

    def assert_geometry_units(self, path, expect_bare):
        checked = 0
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.startswith("X"):
                continue
            for token in line.split():
                if "=" not in token:
                    continue
                name, value = token.split("=", 1)
                if name.lower() not in self.dimensional_properties:
                    continue
                checked += 1
                is_bare = self.bare_decimal.fullmatch(value) is not None
                self.assertEqual(
                    is_bare,
                    expect_bare,
                    "{}:{} has wrong view units: {}={}".format(
                        path, line_number, name, value
                    ),
                )
        self.assertGreater(checked, 0, "No device geometry found in {}".format(path))

    def check_view_selection(self):
        from openram.base.hierarchy_spice import spice

        tech_dir = Path(OPTS.openram_tech)
        for cell_name in self.custom_cells:
            with self.subTest(cell=cell_name, check="view-selection"):
                cell = spice(cell_name, cell_name)
                simulation_path = tech_dir / "sp_lib" / (cell_name + ".sp")
                lvs_path = tech_dir / "lvs_lib" / (cell_name + ".sp")
                self.assertEqual(Path(cell.sp_file), simulation_path)
                self.assertEqual(Path(cell.lvs_file), lvs_path)
                self.assertTrue(hasattr(cell, "lvs"))
                self.assertEqual(
                    self.subckt_pins(cell.spice, cell_name),
                    self.subckt_pins(cell.lvs, cell_name),
                )
                self.assert_geometry_units(simulation_path, expect_bare=True)
                self.assert_geometry_units(lvs_path, expect_bare=False)

    def check_ngspice_operating_points(self):
        ngspice = shutil.which("ngspice")
        if not ngspice:
            self.skipTest("ngspice is not installed")

        pdk_root = Path(os.environ["PDK_ROOT"])
        model = pdk_root / "sky130A" / "libs.tech" / "ngspice" / "sky130.lib.spice"
        self.assertTrue(model.is_file(), "Missing SKY130 ngspice model {}".format(model))

        tech_dir = Path(OPTS.openram_tech)
        includes = [
            '.include "{}"'.format(tech_dir / "sp_lib" / (name + ".sp"))
            for name in self.custom_cells
        ]
        deck_lines = [
            "* SKY130 custom simulation-view operating-point smoke test",
            '.lib "{}" tt'.format(model),
            *includes,
            "Vvdd vdd 0 1.8",
            "Vcell_bl cell_bl 0 0",
            "Vcell_br cell_br 0 1.8",
            "Vcell_wl cell_wl 0 1.8",
            "Xcell cell_bl cell_br cell_wl vdd 0 sky130_custom_cell",
            "Vdummy_bl dummy_bl 0 0",
            "Vdummy_br dummy_br 0 1.8",
            "Vdummy_wl dummy_wl 0 1.8",
            "Xdummy dummy_bl dummy_br dummy_wl vdd 0 sky130_custom_dummy",
            "Vreplica_bl replica_bl 0 0",
            "Vreplica_br replica_br 0 1.8",
            "Vreplica_wl replica_wl 0 1.8",
            "Xreplica replica_bl replica_br replica_wl vdd 0 sky130_custom_replica",
            "Va a 0 0",
            "Vb b 0 0",
            "Vc c 0 0",
            "Vd d 0 0",
            "Xnand2 a b nand2_z vdd 0 sky130_custom_nand2_dec",
            "Xnand3 a b c nand3_z vdd 0 sky130_custom_nand3_dec",
            "Xnand4 a b c d nand4_z vdd 0 sky130_custom_nand4_dec",
            ".op",
            ".end",
        ]

        with tempfile.TemporaryDirectory(prefix="openram-sky130-custom-spice-") as temp:
            temp_dir = Path(temp)
            (temp_dir / ".spiceinit").write_text(
                "set num_threads=1\nset ngbehavior=hsa\nset ng_nomodcheck\n",
                encoding="utf-8",
            )
            deck = temp_dir / "custom_views.sp"
            log = temp_dir / "custom_views.log"
            deck.write_text("\n".join(deck_lines) + "\n", encoding="utf-8")
            environment = os.environ.copy()
            environment["HOME"] = str(temp_dir)
            result = subprocess.run(
                [ngspice, "-b", "-o", str(log), str(deck)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=environment,
                timeout=180,
            )
            log_text = log.read_text(encoding="utf-8", errors="replace")
            self.assertEqual(result.returncode, 0, result.stdout + "\n" + log_text)
            self.assertNotIn("could not find a valid modelname", log_text.lower())
            self.assertNotIn("simulation interrupted due to error", log_text.lower())
            self.assertIn("total analysis time", log_text.lower())

    def check_netgen_lvs_views(self):
        from openram import verify

        if not OPTS.lvs_exe or OPTS.lvs_exe[0] != "netgen":
            self.skipTest("Netgen LVS is not selected")
        tech_dir = Path(OPTS.openram_tech)
        for cell_name in self.custom_cells:
            with self.subTest(cell=cell_name, check="netgen-lvs"):
                gds = tech_dir / "gds_lib" / (cell_name + ".gds")
                lvs = tech_dir / "lvs_lib" / (cell_name + ".sp")
                self.assertTrue(gds.is_file(), "Missing {}".format(gds))
                self.assertTrue(lvs.is_file(), "Missing {}".format(lvs))
                self.assertEqual(verify.run_drc(cell_name, str(gds), str(lvs)), 0)
                self.assertEqual(verify.run_lvs(cell_name, str(gds), str(lvs)), 0)

    def runTest(self):
        if OPTS.tech_name != "sky130":
            self.skipTest("SKY130-specific custom-cell test")
        config_file = "{}/tests/configs/config".format(os.environ["OPENRAM_HOME"])
        openram.init_openram(config_file, is_unit_test=True)
        try:
            self.check_view_selection()
            self.check_ngspice_operating_points()
            self.check_netgen_lvs_views()
        finally:
            openram.end_openram()


if __name__ == "__main__":
    (OPTS, args) = openram.parse_args()
    del sys.argv[1:]
    header(__file__, OPTS.tech_name)
    unittest.main(testRunner=debugTestRunner())

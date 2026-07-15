#!/usr/bin/env python3
# See LICENSE for licensing information.
#
# Copyright (c) 2016-2026 Regents of the University of California, Santa Cruz
# All rights reserved.

import importlib
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from common import make_openram_package
make_openram_package()

from testutils import debugTestRunner, header, openram_test

import openram
from openram import OPTS
from openram.options import options


class verify_process_safety_test(openram_test):
    """A failed verifier must never reuse an earlier passing artifact."""

    @classmethod
    def setUpClass(cls):
        config_file = "{}/tests/configs/config_front_end".format(
            os.getenv("OPENRAM_HOME"))
        openram.init_openram(config_file, is_unit_test=True)
        cls.magic = importlib.import_module("openram.verify.magic")

    @classmethod
    def tearDownClass(cls):
        openram.end_openram()

    def setUp(self):
        super().setUp()
        self.tempdir = tempfile.TemporaryDirectory(
            prefix="openram-verify-safety-")
        self.saved_options = {
            "openram_temp": OPTS.openram_temp,
            "use_nix": OPTS.use_nix,
            "verbose_level": OPTS.verbose_level,
            "verify_cache": OPTS.verify_cache,
            "output_name": OPTS.output_name,
        }
        OPTS.openram_temp = self.tempdir.name + os.sep
        OPTS.use_nix = False
        OPTS.verbose_level = 0
        OPTS.output_name = ""

    def tearDown(self):
        for name, value in self.saved_options.items():
            setattr(OPTS, name, value)
        self.tempdir.cleanup()
        super().tearDown()

    def write_script(self, script, body):
        path = os.path.join(OPTS.openram_temp, "run_{}.sh".format(script))
        with open(path, "w") as stream:
            stream.write("#!/bin/sh\n")
            stream.write(body)
            if not body.endswith("\n"):
                stream.write("\n")
        os.chmod(path, 0o700)

    def test_verification_cache_is_opt_in(self):
        self.assertIs(options.verify_cache, False)
        self.assertIs(OPTS.verify_cache, False)

    def test_failed_magic_extraction_rejects_stale_outputs(self):
        cell_name = "stale_extract"
        extracted = os.path.join(OPTS.openram_temp,
                                 cell_name + ".spice")
        report = os.path.join(OPTS.openram_temp,
                              cell_name + ".lvs.report")
        lvs_started = os.path.join(OPTS.openram_temp, "lvs_started")
        with open(extracted, "w") as stream:
            stream.write("* stale passing extraction\n")
        with open(report, "w") as stream:
            stream.write("Subcircuit summary:\nCircuits match uniquely.\n")

        def write_magic_scripts(*args, **kwargs):
            self.write_script("extract", "exit 23")
            self.write_script(
                "drc", "echo 'Total DRC errors found: 0'")

        def write_netgen_script(*args, **kwargs):
            self.write_script("lvs", "touch {}".format(lvs_started))

        OPTS.verify_cache = False
        with mock.patch.object(self.magic, "write_drc_lvs_scripts",
                               side_effect=write_magic_scripts), \
                mock.patch.object(self.magic, "write_lvs_script",
                                  side_effect=write_netgen_script):
            with self.assertRaises(subprocess.CalledProcessError) as error:
                self.magic.run_drc_lvs(cell_name, "unused.gds",
                                       "unused.sp")

        self.assertEqual(error.exception.returncode, 23)
        self.assertFalse(os.path.exists(extracted))
        self.assertFalse(os.path.exists(report))
        self.assertFalse(os.path.exists(lvs_started))

    def test_failed_netgen_rejects_stale_report(self):
        cell_name = "stale_lvs"
        report = os.path.join(OPTS.openram_temp,
                              cell_name + ".lvs.report")
        with open(report, "w") as stream:
            stream.write("Subcircuit summary:\nCircuits match uniquely.\n")

        def write_netgen_script(*args, **kwargs):
            self.write_script("lvs", "exit 31")

        with mock.patch.object(self.magic, "write_lvs_script",
                               side_effect=write_netgen_script):
            with self.assertRaises(subprocess.CalledProcessError) as error:
                self.magic.run_lvs(cell_name, "unused.gds", "unused.sp")

        self.assertEqual(error.exception.returncode, 31)
        self.assertFalse(os.path.exists(report))

    def test_successful_parallel_flow_still_parses_fresh_outputs(self):
        cell_name = "fresh_pass"

        def write_magic_scripts(*args, **kwargs):
            self.write_script(
                "extract", ": > {}.spice".format(cell_name))
            self.write_script(
                "drc", "echo 'Total DRC errors found: 0'")

        def write_netgen_script(*args, **kwargs):
            self.write_script(
                "lvs",
                "printf 'Subcircuit summary:\\nCircuits match uniquely.\\n' "
                "> {}.lvs.report".format(cell_name))

        OPTS.verify_cache = False
        with mock.patch.object(self.magic, "write_drc_lvs_scripts",
                               side_effect=write_magic_scripts), \
                mock.patch.object(self.magic, "write_lvs_script",
                                  side_effect=write_netgen_script):
            result = self.magic.run_drc_lvs(cell_name, "unused.gds",
                                            "unused.sp")

        self.assertEqual(result, (0, 0))
        self.assertTrue(os.path.isfile(os.path.join(
            OPTS.openram_temp, cell_name + ".spice")))
        self.assertTrue(os.path.isfile(os.path.join(
            OPTS.openram_temp, cell_name + ".lvs.report")))


if __name__ == "__main__":
    (OPTS, args) = openram.parse_args()
    del sys.argv[1:]
    header(__file__, OPTS.tech_name)
    unittest.main(testRunner=debugTestRunner())

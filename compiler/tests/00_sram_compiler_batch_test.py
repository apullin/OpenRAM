#!/usr/bin/env python3
"""Focused safety and equivalence tests for sram_compiler batch mode."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


class sram_compiler_batch_test(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.repo = Path(__file__).resolve().parents[2]
        cls.compiler = cls.repo / "sram_compiler.py"

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="openram_batch_test_"))
        self.addCleanup(shutil.rmtree, self.work, True)

    def write_config(self, name, output_name=None, output_path=None,
                     extra=""):
        output_name = output_name or name
        output_path = output_path or self.work / (name + "_out")
        temp_path = self.work / "tmp" / name
        config = self.work / (name + ".py")
        config.write_text(textwrap.dedent("""\
            word_size = 2
            num_words = 16
            words_per_row = 1
            tech_name = "scn4m_subm"
            output_name = {output_name!r}
            output_path = {output_path!r}
            openram_temp = {temp_path!r}
            use_nix = False
            netlist_only = True
            check_lvsdrc = False
            analytical_delay = True
            nominal_corner_only = True
            output_datasheet_info = False
            deterministic = True
            {extra}
            """).format(output_name=str(output_name),
                          output_path=str(output_path),
                          temp_path=str(temp_path), extra=extra))
        return config

    def run_compiler(self, *configs, verbose=False):
        env = os.environ.copy()
        env["OPENRAM_HOME"] = str(self.repo / "compiler")
        command = [sys.executable, str(self.compiler)]
        if verbose:
            command.append("-v")
        command.extend(str(config) for config in configs)
        return subprocess.run(command, cwd=self.repo, env=env, text=True,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, check=False)

    def assert_rejected_pair(self, field, first_extra, second_extra):
        first = self.write_config("reject_first_" + field,
                                  extra=first_extra)
        second = self.write_config("reject_second_" + field,
                                   extra=second_extra)
        result = self.run_compiler(first, second)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("BATCH REJECTED:", result.stdout)
        self.assertIn(field, result.stdout)
        self.assertFalse((self.work / ("reject_first_" + field +
                                      "_out")).exists(), result.stdout)
        self.assertFalse((self.work / ("reject_second_" + field +
                                      "_out")).exists(), result.stdout)

    def test_rejects_mixed_process_global_modes(self):
        cases = (
            ("tech_name", 'tech_name = "scn4m_subm"',
             'tech_name = "sky130"'),
            ("check_lvsdrc", "check_lvsdrc = False",
             "netlist_only = False\ncheck_lvsdrc = True"),
            ("drc_name", 'drc_name = "magic"',
             'drc_name = "klayout"'),
            ("spice_name", 'spice_name = "ngspice"',
             'spice_name = "Xyce"'),
            ("analytical_delay", "analytical_delay = True",
             "analytical_delay = False"),
        )
        for field, first, second in cases:
            with self.subTest(field=field):
                self.assert_rejected_pair(field, first, second)

    def test_rejects_duplicate_resolved_output(self):
        output = self.work / "shared_output"
        first = self.write_config("duplicate_first", output_name="same",
                                  output_path=output)
        second = self.write_config("duplicate_second", output_name="same",
                                   output_path=output)
        result = self.run_compiler(first, second)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("BATCH REJECTED:", result.stdout)
        self.assertIn("same output destination", result.stdout)
        self.assertFalse(output.exists(), result.stdout)

    def test_failed_middle_is_cleaned_and_later_config_runs(self):
        first = self.write_config("failure_first")
        middle = self.write_config(
            "failure_middle", extra='control_logic = "batch_failure_module"')
        last = self.write_config("failure_last")
        failure_module = self.work / "batch_failure_module.py"
        failure_module.write_text(textwrap.dedent("""\
            from openram import OPTS
            from pathlib import Path
            Path(OPTS.openram_temp, "must_be_cleaned").write_text("leak")
            raise RuntimeError("intentional batch test failure")
            """))

        result = self.run_compiler(first, middle, last, verbose=True)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("FAILED: {}".format(middle), result.stdout)
        self.assertIn("intentional batch test failure", result.stdout)
        self.assertTrue((self.work / "failure_first_out" /
                         "failure_first.v").is_file(), result.stdout)
        self.assertFalse((self.work / "failure_middle_out" /
                          "failure_middle.v").exists(), result.stdout)
        self.assertTrue((self.work / "failure_last_out" /
                         "failure_last.v").is_file(), result.stdout)
        self.assertFalse((self.work / "tmp" / "failure_middle" /
                          "must_be_cleaned").exists(), result.stdout)

    def test_safe_analytical_batch_matches_solo(self):
        batch_configs = []
        solo_configs = []
        (self.work / "batch").mkdir()
        (self.work / "solo").mkdir()
        for index in range(2):
            name = "equiv_{}".format(index)
            batch_configs.append(self.write_config(
                "batch_" + name, output_name=name,
                output_path=self.work / "batch" / name))
            solo_configs.append(self.write_config(
                "solo_" + name, output_name=name,
                output_path=self.work / "solo" / name))

        batch_result = self.run_compiler(*batch_configs)
        self.assertEqual(batch_result.returncode, 0, batch_result.stdout)
        for config in solo_configs:
            solo_result = self.run_compiler(config)
            self.assertEqual(solo_result.returncode, 0, solo_result.stdout)

        for index in range(2):
            name = "equiv_{}".format(index)
            batch_dir = self.work / "batch" / name
            solo_dir = self.work / "solo" / name
            for suffix in (".sp", ".lvs.sp", ".v"):
                batch_data = (batch_dir / (name + suffix)).read_bytes()
                solo_data = (solo_dir / (name + suffix)).read_bytes()
                self.assertEqual(batch_data, solo_data,
                                 "{} differs for {}".format(suffix, name))


if __name__ == "__main__":
    unittest.main(verbosity=2)

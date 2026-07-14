#!/usr/bin/env python3
# See LICENSE for licensing information.
#
# Copyright (c) 2026
# All rights reserved.

import os
import sys
import unittest

from testutils import *

import openram
from openram import OPTS, debug
from openram.sram_factory import factory


class sram_1bank_internal_filler_test(openram_test):
    """Keep technology tiling columns private to the generated SRAM."""

    def runTest(self):
        config_file = "{}/tests/configs/config".format(os.getenv("OPENRAM_HOME"))
        OPTS.check_lvsdrc = False
        openram.init_openram(config_file, is_unit_test=True)
        OPTS.netlist_only = True

        from openram import sram, sram_config

        for word_size, words_per_row in ((8, 4), (32, 2)):
            with self.subTest(word_size=word_size):
                factory.reset()
                c = sram_config(word_size=word_size,
                                num_words=64,
                                num_banks=1,
                                words_per_row=words_per_row)
                macro = sram(c, "sram_64x{}_exact_ports".format(word_size))
                bank = macro.s.bank
                replica_cols = int(bank.has_rbl) * len(bank.all_ports)
                expected_fillers = (-(c.num_cols + c.num_spare_cols + replica_cols)
                                    % c.array_col_multiple)

                self.assertEqual(bank.num_filler_cols, expected_fillers)
                self.assertEqual(expected_fillers, 1 if OPTS.tech_name == "sky130" else 0)
                self.assertEqual(bank.bitcell_array.column_size,
                                 c.num_cols + c.num_spare_cols + expected_fillers)
                self.assertEqual(bank.port_data[0].num_filler_cols, expected_fillers)

                # Fillers are precharged bitcell columns only.  They are not data,
                # repair, address, write-mask, or write-enable resources.
                port_data_pins = set(bank.port_data[0].pins)
                for filler in range(expected_fillers):
                    self.assertIn("fillerbl_{}".format(filler), port_data_pins)
                    self.assertIn("fillerbr_{}".format(filler), port_data_pins)
                self.assertNotIn("din_{}".format(word_size), port_data_pins)
                self.assertNotIn("dout_{}".format(word_size), port_data_pins)
                self.assertFalse(any("spare_wen" in pin for pin in port_data_pins))

                top_pins = set(macro.s.pins)
                self.assertEqual({"din0[{}]".format(bit) for bit in range(word_size)},
                                 {pin for pin in top_pins if pin.startswith("din0[")})
                self.assertEqual({"dout0[{}]".format(bit) for bit in range(word_size)},
                                 {pin for pin in top_pins if pin.startswith("dout0[")})
                self.assertEqual({"addr0[{}]".format(bit) for bit in range(6)},
                                 {pin for pin in top_pins if pin.startswith("addr0[")})
                self.assertFalse(any("filler" in pin or "spare_wen" in pin
                                     for pin in top_pins))

                vname = os.path.join(OPTS.openram_temp,
                                     "sram_64x{}_exact_ports.v".format(word_size))
                macro.verilog_write(vname)
                with open(vname) as vf:
                    verilog = vf.read()
                self.assertIn("parameter DATA_WIDTH = {} ;".format(word_size), verilog)
                self.assertIn("parameter ADDR_WIDTH = 6 ;", verilog)
                self.assertNotIn("spare_wen", verilog)
                self.assertNotIn("filler", verilog)

        # A requested repair column remains public and, because it also solves
        # the tiling parity, does not acquire a redundant private filler.
        factory.reset()
        repair_config = sram_config(word_size=8,
                                    num_words=64,
                                    num_banks=1,
                                    words_per_row=4,
                                    num_spare_cols=1)
        repair_macro = sram(repair_config, "sram_64x8_repair_column")
        repair_bank = repair_macro.s.bank
        self.assertEqual(repair_bank.num_filler_cols, 0)
        self.assertIn("spare_wen0", repair_macro.s.pins)
        self.assertIn("din0[8]", repair_macro.s.pins)
        self.assertIn("dout0[8]", repair_macro.s.pins)
        repair_vname = os.path.join(OPTS.openram_temp, "sram_64x8_repair_column.v")
        repair_macro.verilog_write(repair_vname)
        with open(repair_vname) as vf:
            repair_verilog = vf.read()
        self.assertIn("parameter DATA_WIDTH = 9 ;", repair_verilog)
        self.assertIn("spare_wen0", repair_verilog)

        # A global/local hierarchy needs each local array legalized separately;
        # reject the unsupported combination instead of creating bad tiling.
        factory.reset()
        OPTS.local_array_size = 16
        local_config = sram_config(word_size=8,
                                   num_words=64,
                                   num_banks=1,
                                   words_per_row=4)
        try:
            with self.assertRaises(AssertionError):
                sram(local_config, "sram_64x8_unsupported_local_filler")
        finally:
            OPTS.local_array_size = 0
            factory.reset()

        openram.end_openram()


if __name__ == "__main__":
    (OPTS, args) = openram.parse_args()
    del sys.argv[1:]
    header(__file__, OPTS.tech_name)
    unittest.main(testRunner=debugTestRunner())

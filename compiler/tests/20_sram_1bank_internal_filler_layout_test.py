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
from openram import OPTS
from openram.sram_factory import factory


class sram_1bank_internal_filler_layout_test(openram_test):
    """Construct and route exact-ABI arrays with private physical fillers."""

    def set_ports(self, rw, read, write):
        OPTS.num_rw_ports = rw
        OPTS.num_r_ports = read
        OPTS.num_w_ports = write
        factory.reset()
        openram.setup_bitcell()

    def assert_rbl_connection(self, bank, port):
        array_name = "rbl_bl_{0}_{0}".format(port)
        array_connections = dict(zip(bank.bitcell_array_inst.mod.pins,
                                     bank.bitcell_array_inst.get_connections()))
        port_connections = dict(zip(bank.port_data_inst[port].mod.pins,
                                    bank.port_data_inst[port].get_connections()))
        self.assertEqual(array_connections[array_name], port_connections["rbl_bl"])

        array_pin = bank.bitcell_array_inst.get_pin(array_name)
        port_pin = bank.port_data_inst[port].get_pin("rbl_bl")
        self.assertEqual(array_pin.layer, port_pin.layer)

        physical_last = bank.bitcell_array.column_size - 1
        if port == 0:
            array_data_pin = bank.bitcell_array_inst.get_pin("bl_0_0")
            port_data_pin = bank.port_data_inst[port].get_pin("bl_0")
            self.assertLess(array_pin.cx(), array_data_pin.cx())
            self.assertLess(port_pin.cx(), port_data_pin.cx())
        else:
            array_data_pin = bank.bitcell_array_inst.get_pin(
                "bl_{0}_{1}".format(port, physical_last))
            port_data = bank.port_data[port]
            if port_data.num_filler_cols:
                last_pin_name = "fillerbl_{}".format(port_data.num_filler_cols - 1)
            elif port_data.num_spare_cols:
                last_pin_name = "sparebl_{}".format(port_data.num_spare_cols - 1)
            else:
                last_pin_name = "bl_{}".format(port_data.num_cols - 1)
            port_data_pin = bank.port_data_inst[port].get_pin(last_pin_name)
            self.assertGreater(array_pin.cx(), array_data_pin.cx())
            self.assertGreater(port_pin.cx(), port_data_pin.cx())

    def assert_exact_layout(self, macro, word_size, words_per_row):
        bank = macro.s.bank
        physical_cols = word_size * words_per_row + bank.num_filler_cols
        self.assertEqual(bank.num_filler_cols, 1)
        self.assertEqual(bank.bitcell_array.column_size, physical_cols)
        self.assertGreater(macro.s.width, 0)
        self.assertGreater(macro.s.height, 0)

        port_data = bank.port_data[0]
        self.assertEqual(len(port_data.data_bit_offsets), word_size * words_per_row)
        self.assertEqual(len(port_data.datapath_bit_offsets), word_size * words_per_row)
        self.assertEqual(len(port_data.physical_bit_offsets), physical_cols)
        self.assertEqual(len(port_data.sense_amp_array.local_insts), word_size)
        self.assertEqual(len(port_data.write_driver_array.local_insts), word_size)
        self.assertEqual(len(port_data.precharge_array.local_insts), physical_cols + 1)
        self.assertGreater(port_data.get_pin("fillerbl_0").cx(),
                           port_data.get_pin("bl_{}".format(physical_cols - 2)).cx())
        self.assert_rbl_connection(bank, 0)

        top_pins = set(macro.s.pins)
        self.assertEqual({"din0[{}]".format(bit) for bit in range(word_size)},
                         {pin for pin in top_pins if pin.startswith("din0[")})
        self.assertEqual({"dout0[{}]".format(bit) for bit in range(word_size)},
                         {pin for pin in top_pins if pin.startswith("dout0[")})
        self.assertFalse(any("filler" in pin or "spare_wen" in pin
                             for pin in top_pins))

    def runTest(self):
        config_file = "{}/tests/configs/config".format(os.getenv("OPENRAM_HOME"))
        OPTS.check_lvsdrc = False
        openram.init_openram(config_file, is_unit_test=True)
        OPTS.check_lvsdrc = False
        OPTS.inline_lvsdrc = False
        OPTS.netlist_only = False
        OPTS.route_supplies = False
        OPTS.local_array_size = 0

        from openram import sram, sram_config

        self.set_ports(rw=1, read=0, write=0)
        for word_size, words_per_row in ((8, 4), (32, 2)):
            with self.subTest(topology="1rw", word_size=word_size):
                factory.reset()
                config = sram_config(word_size=word_size,
                                     num_words=64,
                                     num_banks=1,
                                     words_per_row=words_per_row)
                macro = sram(config,
                             "sram_64x{}_exact_layout".format(word_size))
                self.assert_exact_layout(macro, word_size, words_per_row)

        # Two replicas already make an even-width exact-ABI array legal. Check
        # that both RBLs remain connected and ordered on the correct sides.
        self.set_ports(rw=1, read=1, write=0)
        dual_config = sram_config(word_size=8,
                                  num_words=64,
                                  num_banks=1,
                                  words_per_row=4)
        dual_macro = sram(dual_config, "sram_64x8_dual_exact_layout")
        dual_bank = dual_macro.s.bank
        self.assertEqual(dual_bank.num_filler_cols, 0)
        self.assertGreater(dual_macro.s.width, 0)
        self.assertGreater(dual_macro.s.height, 0)
        self.assert_rbl_connection(dual_bank, 0)
        self.assert_rbl_connection(dual_bank, 1)
        self.assertLess(dual_bank.port_data[0].get_pin("rbl_bl").cx(),
                        dual_bank.port_data[0].get_pin("bl_0").cx())
        self.assertGreater(dual_bank.port_data[1].get_pin("rbl_bl").cx(),
                           dual_bank.port_data[1].get_pin("bl_31").cx())

        # An explicit repair column makes the dual-port array odd again.  This
        # exercises a right-side RBL after both a public spare and a filler.
        factory.reset()
        repair_config = sram_config(word_size=8,
                                    num_words=64,
                                    num_banks=1,
                                    words_per_row=4,
                                    num_spare_cols=1)
        repair_macro = sram(repair_config, "sram_64x8_dual_repair_layout")
        repair_bank = repair_macro.s.bank
        self.assertEqual(repair_bank.num_filler_cols, 1)
        self.assert_rbl_connection(repair_bank, 0)
        self.assert_rbl_connection(repair_bank, 1)
        for port_data in repair_bank.port_data:
            self.assertEqual(len(port_data.data_bit_offsets), 32)
            self.assertEqual(len(port_data.datapath_bit_offsets), 33)
            self.assertEqual(len(port_data.physical_bit_offsets), 34)
            self.assertEqual(len(port_data.sense_amp_array.local_insts), 9)
        self.assertEqual(len(repair_bank.port_data[0].write_driver_array.local_insts), 9)
        self.assertGreater(repair_bank.port_data[1].get_pin("rbl_bl").cx(),
                           repair_bank.port_data[1].get_pin("fillerbl_0").cx())

        openram.end_openram()


if __name__ == "__main__":
    (OPTS, args) = openram.parse_args()
    del sys.argv[1:]
    header(__file__, OPTS.tech_name)
    unittest.main(testRunner=debugTestRunner())

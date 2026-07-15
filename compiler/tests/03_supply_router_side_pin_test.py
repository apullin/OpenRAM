#!/usr/bin/env python3
# See LICENSE for licensing information.
#
# Copyright (c) 2016-2026 Regents of the University of California, Santa Cruz
# All rights reserved.

import os
import sys
import unittest
from unittest import mock

from common import make_openram_package
make_openram_package()

from testutils import *

import openram
from openram import debug


class supply_router_side_pin_test(openram_test):

    def runTest(self):
        config_file = "{}/tests/configs/config".format(
            os.getenv("OPENRAM_HOME"))
        openram.init_openram(config_file, is_unit_test=True)

        from openram.base.vector import vector
        from openram import OPTS
        from openram.router.rust_route import supply_route
        from openram.router.supply_router import supply_router

        class SideShape:
            def __init__(self, y):
                self.rect = [vector(0, y), vector(10, y + 1)]

        class FakePin:
            def __init__(self, name, y):
                self.name = name
                self.lpp = "m3"
                self.rect = [vector(1, y), vector(2, y + 1)]

        def make_router(side):
            router = supply_router.__new__(supply_router)
            router.pin_type = side
            router.new_pins = {}
            router.pins = {"vdd": set(), "gnd": set()}
            router.fake_pins = []
            router.blockages = []
            router.all_pins = []

            for method in ("prepare_gds_reader", "find_pins",
                           "find_blockages", "find_vias",
                           "convert_vias", "convert_blockages"):
                setattr(router, method, mock.Mock())
            router.get_mst_pairs = mock.Mock(return_value=[])
            router.get_layer = mock.Mock(return_value="m3")
            router.inflate_shape = mock.Mock(side_effect=lambda pin: pin)

            fake_vdd = FakePin("vdd", 0)
            fake_gnd = FakePin("gnd", 2)
            router.add_side_pin = mock.Mock(side_effect=(
                (SideShape(0), [fake_vdd]),
                (SideShape(2), [fake_gnd]),
            ))
            return router, fake_vdd, fake_gnd

        old_use_rust_router = OPTS.use_rust_router
        try:
            # Preserve the original Python-path regression even when a Rust
            # extension is present in the checkout.
            OPTS.use_rust_router = False
            for side in ("left", "right", "top", "bottom"):
                with self.subTest(backend="python", side=side):
                    router, fake_vdd, fake_gnd = make_router(side)

                    router.route()

                    self.assertEqual(
                        router.add_side_pin.call_args_list,
                        [mock.call("vdd", side), mock.call("gnd", side)])
                    self.assertEqual([p.name for p in router.new_pins["vdd"]],
                                     ["vdd"])
                    self.assertEqual([p.name for p in router.new_pins["gnd"]],
                                     ["gnd"])
                    self.assertIn(fake_vdd, router.pins["vdd"])
                    self.assertIn(fake_gnd, router.pins["gnd"])
                    self.assertEqual(router.fake_pins, [fake_vdd, fake_gnd])
                    self.assertEqual(len(router.blockages), 2)

            # The store-backed path must publish the same rails and fake MST
            # targets, but its rail blockages belong to RouterStore.
            for side in ("left", "right", "top", "bottom"):
                with self.subTest(backend="rust", side=side):
                    router, fake_vdd, fake_gnd = make_router(side)
                    context = mock.Mock()
                    context.convert.side_effect = lambda shape: (
                        "converted", shape.name)
                    with mock.patch(
                            "openram.router.rust_route.store_context",
                            return_value=context):
                        supply_route(router)

                    self.assertEqual(
                        router.add_side_pin.call_args_list,
                        [mock.call("vdd", side), mock.call("gnd", side)])
                    self.assertEqual([p.name for p in router.new_pins["vdd"]],
                                     ["vdd"])
                    self.assertEqual([p.name for p in router.new_pins["gnd"]],
                                     ["gnd"])
                    self.assertIn(fake_vdd, router.pins["vdd"])
                    self.assertIn(fake_gnd, router.pins["gnd"])
                    self.assertEqual(router.fake_pins, [fake_vdd, fake_gnd])
                    self.assertEqual(router.blockages, [])
                    self.assertEqual(
                        context.store.append_blockage.call_count, 2)
        finally:
            OPTS.use_rust_router = old_use_rust_router

        openram.end_openram()


if __name__ == "__main__":
    (OPTS, args) = openram.parse_args()
    del sys.argv[1:]
    header(__file__, OPTS.tech_name)
    unittest.main(testRunner=debugTestRunner())

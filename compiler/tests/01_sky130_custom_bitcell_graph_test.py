#!/usr/bin/env python3
# See LICENSE for licensing information.
#
# Copyright (c) 2016-2026 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.
#
import ast
import sys
import unittest
from pathlib import Path


class sky130_custom_bitcell_graph_test(unittest.TestCase):
    """Check the custom bitcell pin classes used to build timing edges."""

    @staticmethod
    def attribute_name(node):
        names = []
        while isinstance(node, ast.Attribute):
            names.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            names.append(node.id)
        return ".".join(reversed(names))

    def read_assignment(self, tree, assignment_name):
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            if self.attribute_name(node.targets[0]) == assignment_name:
                return ast.literal_eval(node.value)
        self.fail("Missing {}".format(assignment_name))

    def test_read_path_edges_follow_bitcell_pin_roles(self):
        repo_root = Path(__file__).resolve().parents[2]
        config = (
            repo_root
            / "technology"
            / "sky130"
            / "tech"
            / "tech_configs"
            / "tech_custom_cell.py"
        )
        tree = ast.parse(config.read_text(encoding="utf-8"), filename=str(config))
        prefix = "cell_properties.bitcell_1port."
        port_order = self.read_assignment(tree, prefix + "port_order")
        port_types = self.read_assignment(tree, prefix + "port_types")

        self.assertEqual(len(port_order), len(port_types))
        pin_types = dict(zip(port_order, port_types))
        self.assertEqual(
            pin_types,
            {
                "bl": "OUTPUT",
                "br": "OUTPUT",
                "wl": "INPUT",
                "vdd": "POWER",
                "gnd": "GROUND",
            },
        )

        inputs = [pin for pin, pin_type in pin_types.items()
                  if pin_type in ("INPUT", "INOUT")]
        outputs = [pin for pin, pin_type in pin_types.items()
                   if pin_type in ("OUTPUT", "INOUT")]
        graph_edges = {(source, sink) for source in inputs for sink in outputs
                       if source != sink}
        self.assertIn(("wl", "bl"), graph_edges)
        self.assertIn(("wl", "br"), graph_edges)
        self.assertNotIn(("bl", "gnd"), graph_edges)

    def test_storage_nodes_follow_custom_spice_names(self):
        repo_root = Path(__file__).resolve().parents[2]
        config = (
            repo_root
            / "technology"
            / "sky130"
            / "tech"
            / "tech_configs"
            / "tech_custom_cell.py"
        )
        tree = ast.parse(config.read_text(encoding="utf-8"), filename=str(config))
        storage_nets = self.read_assignment(
            tree, "cell_properties.bitcell_1port.storage_nets"
        )

        spice = (
            repo_root
            / "technology"
            / "sky130"
            / "sp_lib"
            / "sky130_custom_cell.sp"
        )
        spice_tokens = set(spice.read_text(encoding="utf-8").split())

        self.assertEqual(storage_nets, ["Q", "Qbar"])
        self.assertTrue(set(storage_nets).issubset(spice_tokens))


if __name__ == "__main__":
    # The OpenRAM Makefile supplies technology-selection options to every
    # test. This source-library check is always SKY130-specific and needs none.
    sys.argv[1:] = []
    unittest.main()

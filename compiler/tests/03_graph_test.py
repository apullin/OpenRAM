#!/usr/bin/env python3
# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California, Santa Cruz
# All rights reserved.
#
import os
import sys
import unittest
from types import SimpleNamespace

from testutils import *

import openram
from openram import debug
from openram import OPTS


class graph_test(openram_test):

    def runTest(self):
        config_file = "{}/tests/configs/config".format(os.getenv("OPENRAM_HOME"))
        openram.init_openram(config_file, is_unit_test=True)

        from openram.base.vector import vector
        from openram.router.graph import graph
        from openram.router.graph_shape import graph_shape
        from openram.router.graph_node import graph_node

        def shape(name, ll, ur):
            return graph_shape(name,
                               [vector(*ll), vector(*ur)],
                               "m1")

        first = shape("first", (1, 1), (2, 2))
        duplicate = shape("duplicate", (1, 1), (2, 2))
        early = shape("early", (3, 3), (4, 4))
        source = shape("source", (6, 6), (7, 7))
        target = shape("target", (8, 8), (9, 9))
        late = shape("late", (11, 11), (12, 12))
        router = SimpleNamespace(blockages=[first, duplicate, early, late])

        route_graph = graph(router)
        route_graph.source = source
        route_graph.target = target
        route_graph.graph_blockages = []
        route_graph.graph_blockage_set = set()

        initial_region = shape("region", (0, 0), (5, 5))
        route_graph.find_graph_blockages(initial_region)
        expected = [first, early, source, target]
        self.assertEqual([id(item) for item in route_graph.graph_blockages],
                         [id(item) for item in expected])
        self.assertFalse(any(item is duplicate
                             for item in route_graph.graph_blockages))

        route_graph.find_graph_blockages(initial_region)
        self.assertEqual([id(item) for item in route_graph.graph_blockages],
                         [id(item) for item in expected])

        expanded_region = shape("region", (0, 0), (13, 13))
        route_graph.find_graph_blockages(expanded_region)
        expected.append(late)
        self.assertEqual([id(item) for item in route_graph.graph_blockages],
                         [id(item) for item in expected])
        self.assertEqual(route_graph.graph_blockage_set,
                         set(route_graph.graph_blockages))

        left = graph_node((0, 0, 0))
        removed = graph_node((1, 0, 0))
        right = graph_node((2, 0, 0))
        removed.add_neighbor(left)
        removed.add_neighbor(right)
        removed.remove = True
        route_graph.nodes = [left, removed, right]

        route_graph.remove_blocked_nodes()
        self.assertEqual([id(item) for item in route_graph.nodes],
                         [id(left), id(right)])
        self.assertEqual(removed.neighbors, [])
        self.assertNotIn(removed, left.neighbors)
        self.assertNotIn(removed, right.neighbors)

        openram.end_openram()


if __name__ == "__main__":
    (OPTS, args) = openram.parse_args()
    del sys.argv[1:]
    header(__file__, OPTS.tech_name)
    unittest.main(testRunner=debugTestRunner())

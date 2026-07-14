#!/usr/bin/env python3
# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California, Santa Cruz
# All rights reserved.
#
import os
import sys
import unittest
from unittest.mock import patch
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
        from openram.router.bbox import bbox as router_bbox
        from openram.router.bbox_node import bbox_node
        from openram.router import graph_utils

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

        via_nodes = [graph_node((0, 0, 0)), graph_node((0, 0, 1))]
        route_graph.graph_vias = []
        with patch.object(route_graph, "is_node_blocked",
                          side_effect=AssertionError("nodes already checked")):
            self.assertFalse(route_graph.is_via_blocked(
                via_nodes, check_blockages=False))
        with patch.object(route_graph, "is_node_blocked",
                          return_value=True) as is_node_blocked:
            self.assertTrue(route_graph.is_via_blocked(via_nodes))
            is_node_blocked.assert_called_once_with(via_nodes[0])

        route_graph.graph_vias = [object()]
        route_graph.via_bbox_tree = SimpleNamespace(
            iterate_point=lambda _point: ())
        with patch.object(route_graph, "is_node_blocked", return_value=False):
            self.assertFalse(route_graph.is_via_blocked(iter(via_nodes)))
        with patch.object(route_graph, "is_node_blocked",
                          side_effect=AssertionError("nodes already checked")):
            self.assertFalse(route_graph.is_via_blocked(
                via_nodes, check_blockages=False))

        class containment_shape(graph_shape):
            def __eq__(self, _other):
                raise AssertionError("contains must not call equality")

        container = containment_shape("container",
                                      [vector(0, 0), vector(4, 4)],
                                      "m1")
        inside = shape("inside", (1, 1), (2, 2))
        equivalent = shape("equivalent", (0, 0), (4, 4))
        other_layer = graph_shape("other_layer",
                                  [vector(0, 0), vector(4, 4)],
                                  "m2")
        self.assertTrue(container.contains(container))
        self.assertTrue(container.contains(inside))
        self.assertTrue(equivalent.contains(shape("same", (0, 0), (4, 4))))
        self.assertFalse(inside.contains(container))
        self.assertFalse(container.contains(other_layer))

        overlapping = shape("overlapping", (3, 3), (5, 5))
        touching = shape("touching", (4, 1), (5, 2))
        x_disjoint = shape("x_disjoint", (5, 1), (6, 2))
        y_disjoint = shape("y_disjoint", (1, 5), (2, 6))
        self.assertTrue(equivalent.overlaps(overlapping))
        self.assertTrue(equivalent.overlaps(touching))
        self.assertFalse(equivalent.overlaps(x_disjoint))
        self.assertFalse(equivalent.overlaps(y_disjoint))
        reversed_rect = graph_shape("reversed",
                                    [vector(4, 4), vector(0, 0)],
                                    "m1")
        spanning = shape("spanning", (2, 2), (5, 5))
        self.assertTrue(reversed_rect.overlaps(spanning))

        shared_lpp = equivalent.lpp
        equal_lpp = tuple([shared_lpp[0], shared_lpp[1]])
        self.assertTrue(equivalent.same_lpp(shared_lpp, shared_lpp))
        self.assertTrue(equivalent.same_lpp(shared_lpp, equal_lpp))
        self.assertTrue(equivalent.same_lpp(shared_lpp,
                                            (shared_lpp[0], None)))
        self.assertFalse(equivalent.same_lpp(shared_lpp,
                                             (shared_lpp[0] + 1, None)))

        class short_circuit_shape(graph_shape):
            def xoverlaps(self, _other):
                return False

            def yoverlaps(self, _other):
                raise AssertionError("disjoint x must short-circuit y")

        short_circuit = short_circuit_shape("short_circuit",
                                            [vector(0, 0), vector(1, 1)],
                                            "m1")
        self.assertFalse(short_circuit.overlaps(equivalent))

        left_bbox = router_bbox(shape("left_bbox", (0, 0), (2, 2)))
        right_bbox = router_bbox(shape("right_bbox", (4, 0), (6, 2)))
        root = bbox_node(left_bbox.merge(right_bbox),
                         bbox_node(left_bbox), bbox_node(right_bbox))
        inserted_bbox = router_bbox(shape("inserted_bbox", (8, 0), (10, 2)))
        area_calls = []
        original_area = router_bbox.area

        def counted_area(item):
            area_calls.append(item)
            return original_area(item)

        with patch.object(router_bbox, "area", counted_area):
            self.assertEqual(root.get_costs(inserted_bbox), (20, 28, 16))
        self.assertEqual(len(area_calls), 7)

        tree_shapes = [
            shape("tree_0", (-3, -1), (-1, 1)),
            shape("tree_1", (0, 0), (2, 2)),
            shape("tree_2", (2, 2), (4, 4)),
            shape("tree_3", (1, -2), (3, -1)),
            shape("tree_4", (5, 0), (6, 3)),
        ]
        tree_boxes = [router_bbox(item) for item in tree_shapes]
        tree_boxes.append(tree_boxes[1])
        incremental = bbox_node(tree_boxes[0])
        for item in tree_boxes[1:]:
            incremental.insert(item)

        merge_calls = []
        original_merge = router_bbox.merge

        def counted_merge(item, other):
            merge_calls.append((item, other))
            return original_merge(item, other)

        with patch.object(bbox_node, "get_costs",
                          side_effect=AssertionError("bulk build used insertion costs")):
            with patch.object(router_bbox, "merge", counted_merge):
                bulk = bbox_node.build(tree_boxes)
        self.assertEqual(len(merge_calls), len(tree_boxes) - 1)
        self.assertIsNone(bbox_node.build([]))
        self.assertIs(bbox_node.build([tree_boxes[0]]).bbox, tree_boxes[0])

        def result_ids(items):
            return sorted(id(item) for item in items)

        for point in [vector(-3, -1), vector(1, 1),
                      vector(2, 2), vector(10, 10)]:
            self.assertEqual(result_ids(bulk.iterate_point(point)),
                             result_ids(incremental.iterate_point(point)))
        query_shapes = [
            shape("query_0", (1.5, 1.5), (2.5, 2.5)),
            shape("query_1", (-4, -2), (-3, -1)),
            shape("query_2", (8, 8), (9, 9)),
        ]
        for query in query_shapes:
            self.assertEqual(result_ids(bulk.iterate_shape(query)),
                             result_ids(incremental.iterate_shape(query)))

        original_drc = graph_utils.tech.drc
        try:
            graph_utils.tech.drc = {"grid": 0.005}
            self.assertEqual(graph_utils.snap(1.23456), 1.235)
            self.assertEqual(graph_utils.snap(vector(1.23456, 2.34567)),
                             vector(1.235, 2.346))
            graph_utils.tech.drc = {"grid": 0.1}
            self.assertEqual(graph_utils.snap(1.23456), 1.2)
            graph_utils.tech.drc = {"grid": 0.0025}
            self.assertEqual(graph_utils.snap(1.23456), 1.2346)
        finally:
            graph_utils.tech.drc = original_drc
            graph_utils.snap(0)

        openram.end_openram()


if __name__ == "__main__":
    (OPTS, args) = openram.parse_args()
    del sys.argv[1:]
    header(__file__, OPTS.tech_name)
    unittest.main(testRunner=debugTestRunner())

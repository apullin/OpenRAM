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
        from openram.router.router import router as router_class
        from openram.router.graph_shape import graph_shape
        from openram.router.graph_node import graph_node
        from openram.router.bbox import bbox as router_bbox
        from openram.router.bbox_node import bbox_node
        from openram.router import graph_utils

        def shape(name, ll, ur, layer="m1"):
            return graph_shape(name,
                               [vector(*ll), vector(*ur)],
                               layer)

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
        with self.assertRaises(AttributeError):
            left.unexpected_attribute = True
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

        def center_tuple(node):
            center = node.center
            return (center.x, center.y, center.z)

        removed_centers = {
            (0, 1, 0), (1, 0, 1), (1, 1, 0), (2, 1, 1),
        }
        blocked_edges = {
            frozenset(((0, 2, 1), (0, 1, 1))),
            frozenset(((2, 2, 0), (1, 2, 0))),
        }
        blocked_vias = {(2, 2)}

        def run_node_generator(x_values, y_values, legacy=False):
            test_graph = graph(SimpleNamespace())
            probe_calls = []
            via_calls = []

            test_graph.is_node_blocked = lambda node: (
                center_tuple(node) in removed_centers)

            def probe_blocked(p1, p2):
                p1_key = (p1.x, p1.y, p1.z)
                p2_key = (p2.x, p2.y, p2.z)
                probe_calls.append((p1_key, p2_key))
                return frozenset((p1_key, p2_key)) in blocked_edges

            def via_blocked(nodes, check_blockages=True):
                centers = tuple(center_tuple(node) for node in nodes)
                via_calls.append((centers, check_blockages))
                return centers[0][:2] in blocked_vias

            test_graph.is_probe_blocked = probe_blocked
            test_graph.is_via_blocked = via_blocked

            if legacy:
                test_graph.nodes = [
                    graph_node((x, y, z))
                    for x in x_values for y in y_values for z in (0, 1)
                ]
                test_graph.mark_blocked_nodes()

                def search(index, condition, shift):
                    base_nodes = test_graph.nodes[index:index + 2]
                    found = [base_nodes[0].remove, base_nodes[1].remove]
                    while condition(index) and not all(found):
                        nodes = test_graph.nodes[
                            index - shift:index - shift + 2]
                        for z in range(2):
                            if not found[z] and not nodes[z].remove:
                                found[z] = True
                                if not probe_blocked(
                                        base_nodes[z].center, nodes[z].center):
                                    base_nodes[z].add_neighbor(nodes[z])
                        index -= shift

                y_len = len(y_values)
                for i in range(0, len(test_graph.nodes), 2):
                    search(i, lambda count: (count / 2) % y_len, 2)
                    search(i, lambda count: (count / 2) >= y_len,
                           y_len * 2)
                    nodes = test_graph.nodes[i:i + 2]
                    if (not nodes[0].remove and not nodes[1].remove and
                            not via_blocked(nodes, check_blockages=False)):
                        nodes[0].add_neighbor(nodes[1])
                test_graph.remove_blocked_nodes()
            else:
                test_graph.generate_graph_nodes(x_values, y_values)

            signature = [
                (center_tuple(node),
                 [center_tuple(neighbor) for neighbor in node.neighbors])
                for node in test_graph.nodes
            ]
            return signature, probe_calls, via_calls

        graph_cases = [
            ([0], [0]),
            ([0], [0, 1, 2, 3]),
            ([0, 1, 2, 3], [0]),
            ([0, 1, 2], [0, 1, 2]),
            ([], [0]),
            ([0], []),
        ]
        for x_values, y_values in graph_cases:
            self.assertEqual(
                run_node_generator(x_values, y_values),
                run_node_generator(x_values, y_values, legacy=True))

        heuristic_targets = [graph_node((-1, 4, 0)),
                             graph_node((5, -2, 1))]
        heuristic = graph._make_heuristic(heuristic_targets)
        for query in [graph_node((1, 1, 1)),
                      graph_node((-1, 4, 0)),
                      graph_node((8, 8, 0))]:
            expected_distance = min(
                target.center.distance(query.center) +
                abs(target.center.z - query.center.z)
                for target in heuristic_targets
            )
            self.assertEqual(heuristic(query), expected_distance)
            self.assertEqual(heuristic(query), expected_distance)

        product_targets = [
            graph_node((x, y, z))
            for x in (-3.5, 2.0)
            for y in (-1.25, 4.0, 9.5)
            for z in (0, 1)
        ]
        product_targets.append(product_targets[0])
        product_heuristic = graph._make_heuristic(product_targets)
        for query in [graph_node((-5, 2, 0)),
                      graph_node((-3.5, -1.25, 0)),
                      graph_node((0.5, 6.5, 1)),
                      graph_node((8, -8, 1))]:
            expected_distance = min(
                abs(target.center.x - query.center.x) +
                abs(target.center.y - query.center.y) +
                abs(target.center.z - query.center.z)
                for target in product_targets
            )
            self.assertEqual(product_heuristic(query), expected_distance)
            self.assertEqual(product_heuristic(query), expected_distance)

        singleton = graph_node((-2.5, 4, 1))
        singleton_heuristic = graph._make_heuristic([singleton, singleton])
        singleton_query = graph_node((3.5, -1, 0))
        self.assertEqual(singleton_heuristic(singleton_query), 12.0)

        self.assertEqual(graph._make_heuristic([])(graph_node((0, 0, 0))),
                         float("inf"))

        class recording_tree:
            def __init__(self, blockages=()):
                self.blockages = blockages
                self.bounds = []

            def iterate_rect(self, *bounds):
                self.bounds.append(bounds)
                return iter(self.blockages)

        probe_router = SimpleNamespace(get_lpp=lambda _z: source.lpp)
        probe_graph = graph(probe_router)
        probe_graph.source = source
        probe_tree = recording_tree()
        other_probe_tree = recording_tree()
        probe_graph.blockage_bbox_trees = [probe_tree, other_probe_tree]
        probe_p1 = SimpleNamespace(x=4, y=3, z=0)
        probe_p2 = SimpleNamespace(x=1, y=3, z=0)
        self.assertFalse(probe_graph.is_probe_blocked(probe_p1, probe_p2))
        self.assertEqual(probe_tree.bounds, [(1, 3, 4, 3)])
        self.assertEqual(other_probe_tree.bounds, [])

        other_layer = shape("blocked", (2, 2), (3, 4), "m2")
        probe_tree.blockages = [other_layer]
        self.assertFalse(probe_graph.is_probe_blocked(probe_p1, probe_p2))

        fixed_blockage = shape("blocked", (2, 2), (3, 4))
        probe_tree.blockages = [fixed_blockage]
        self.assertTrue(probe_graph.is_probe_blocked(probe_p1, probe_p2))

        core_miss = shape(source.name, (10, 10), (11, 11))
        inflated_miss = graph_shape(
            source.name, [vector(0, 2), vector(5, 4)], "m1", core_miss)
        probe_tree.blockages = [inflated_miss]
        self.assertTrue(probe_graph.is_probe_blocked(probe_p1, probe_p2))

        core_hit = shape(source.name, (2, 2), (3, 4))
        inflated_hit = graph_shape(
            source.name, [vector(0, 2), vector(5, 4)], "m1", core_hit)
        probe_tree.blockages = [inflated_hit]
        self.assertFalse(probe_graph.is_probe_blocked(probe_p1, probe_p2))

        rule_router = SimpleNamespace(
            track_wire=2, half_wire=1, track_space=3)
        rule_graph = graph(rule_router)
        rule_graph.blockage_bbox_trees = [None, None]
        rule_node = graph_node((0, 0, 0))
        self.assertFalse(rule_graph.is_node_blocked(rule_node))
        self.assertIsNone(rule_graph._node_blockage_rules)
        rule_graph.blockage_bbox_trees[0] = SimpleNamespace(
            iterate_point=lambda _point: ())
        self.assertFalse(rule_graph.is_node_blocked(rule_node))
        cached_rules = rule_graph._node_blockage_rules
        self.assertEqual(cached_rules[:2], (2, 1))
        rule_router.track_wire = 99
        self.assertFalse(rule_graph.is_node_blocked(rule_node))
        self.assertIs(rule_graph._node_blockage_rules, cached_rules)

        class filtering_point_tree:
            def __init__(self, blockages):
                self.blockages = blockages

            def iterate_point(self, point):
                for item in self.blockages:
                    ll, ur = item.rect
                    if (ll.x <= point.x <= ur.x and
                            ll.y <= point.y <= ur.y):
                        yield item

        def legacy_node_blocked(test_graph, node):
            point = node.center
            x, y, z = point.x, point.y, point.z
            tree = test_graph.blockage_bbox_trees[z]
            if tree is None:
                return False

            def closest(value, values):
                return graph_utils.snap(min(
                    abs(value - other) for other in values))

            wide = test_graph.router.track_wire
            half_wide = test_graph.router.half_wire
            spacing = graph_utils.snap(
                test_graph.router.track_space + half_wide +
                graph_utils.tech.drc["grid"])
            blocked = False
            for blockage in tree.iterate_point(point):
                if test_graph.router.get_zindex(blockage.lpp) != z:
                    continue
                if not test_graph.is_routable(blockage):
                    blocked = True
                    continue
                blockage = blockage.get_core()
                ll, ur = blockage.rect
                if ll.x > x or x > ur.x or ll.y > y or y > ur.y:
                    blocked = True
                    continue
                lengths = [blockage.width(), blockage.height()]
                centers = blockage.center()
                ll, ur = blockage.rect
                safe = [True, True]
                for axis in range(2):
                    if lengths[axis] >= wide:
                        if closest(
                                point[axis], [ll[axis], ur[axis]]) < half_wide:
                            safe[axis] = False
                    elif centers[axis] != point[axis]:
                        safe[axis] = False
                if not all(safe):
                    blocked = True
                    continue
                xs, ys = test_graph.get_safe_pin_values(blockage)
                xdiff = closest(x, xs)
                ydiff = closest(y, ys)
                if xdiff == 0 and ydiff == 0:
                    if blockage in [test_graph.source, test_graph.target]:
                        return False
                elif xdiff < spacing and ydiff < spacing:
                    blocked = True
            return blocked

        node_source = shape("node_source", (0, 0), (4, 4))
        inflated_source = graph_shape(
            "node_source", [vector(-1, -1), vector(5, 5)],
            "m1", node_source)
        node_target = shape("node_target", (20, 20), (24, 24))
        fixed_node_blockage = shape("node_blocked", (-2, -2), (6, 6))
        other_node_layer = shape("node_other", (-2, -2), (6, 6), "m2")
        narrow_source = shape("narrow_source", (0, 0), (1, 4))
        wide_source = shape("wide_source", (0, 0), (10, 10))
        node_cases = [
            (node_source, [other_node_layer], [(2, 2)]),
            (node_source, [fixed_node_blockage], [(2, 2)]),
            (node_source, [fixed_node_blockage, inflated_source], [(2, 2)]),
            (node_source, [inflated_source],
             [(2, 2), (4.5, 2), (0.995, 2), (1, 2)]),
            (narrow_source, [narrow_source], [(0.5, 2), (0.6, 2)]),
            (wide_source, [wide_source], [(1, 1), (2, 2), (9, 9)]),
        ]
        for test_source, blockages, points in node_cases:
            test_router = SimpleNamespace(
                track_wire=2, half_wire=1, track_space=1,
                get_zindex=lambda lpp, m1_lpp=test_source.lpp:
                    int(lpp[0] != m1_lpp[0]))
            test_graph = graph(test_router)
            test_graph.source = test_source
            test_graph.target = node_target
            test_graph.blockage_bbox_trees = [
                filtering_point_tree(blockages), None]
            for px, py in points:
                test_node = graph_node((px, py, 0))
                self.assertEqual(
                    test_graph.is_node_blocked(test_node),
                    legacy_node_blocked(test_graph, test_node))


        split_router = router_class.__new__(router_class)
        split_router.horiz_lpp = fixed_blockage.lpp
        split_router.vert_lpp = other_layer.lpp
        split_graph = graph(split_router)
        split_graph.graph_blockages = [fixed_blockage, other_layer]
        split_graph.graph_vias = []
        split_graph.build_bbox_trees()
        split_point = vector(2.5, 3)
        self.assertEqual(
            list(split_graph.blockage_bbox_trees[0].iterate_point(
                split_point)),
            [fixed_blockage])
        self.assertEqual(
            list(split_graph.blockage_bbox_trees[1].iterate_point(
                split_point)),
            [other_layer])

        split_graph.graph_blockages = [fixed_blockage]
        split_graph.build_bbox_trees()
        self.assertIsNone(split_graph.blockage_bbox_trees[1])
        probe_z1_p1 = SimpleNamespace(x=4, y=3, z=1)
        probe_z1_p2 = SimpleNamespace(x=1, y=3, z=1)
        self.assertFalse(split_graph.is_probe_blocked(
            probe_z1_p1, probe_z1_p2))

        one_layer_router = router_class.__new__(router_class)
        one_layer_router.horiz_lpp = fixed_blockage.lpp
        one_layer_router.vert_lpp = fixed_blockage.lpp
        one_layer_graph = graph(one_layer_router)
        one_layer_graph.source = source
        one_layer_graph.graph_blockages = [fixed_blockage]
        one_layer_graph.graph_vias = []
        one_layer_graph.build_bbox_trees()
        self.assertTrue(one_layer_graph.is_probe_blocked(
            probe_z1_p1, probe_z1_p2))

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

        blockage_router = router_class.__new__(router_class)
        m1_absorb = shape("m1_absorb", (0, 0), (2, 1))
        m2_absorb = shape("m2_absorb", (10, 0), (11, 1), "m2")
        m1_keep = shape("m1_keep", (20, 0), (24, 1))
        m2_keep = shape("m2_keep", (30, 0), (31, 1), "m2")
        blockage_router.vert_lpp = m1_absorb.lpp
        blockage_router.horiz_lpp = m2_absorb.lpp
        blockage_router.inflate_shape = lambda item: item
        blockage_router.all_pins = {
            shape("m1_pin", (40, 0), (44, 1))
        }
        blockage_router.blockages = [m1_absorb, m2_absorb,
                                     m1_keep, m2_keep]
        new_blockages = [
            shape("boundary", (12, 0), (14, 1), "m2"),
            shape("boundary", (2, 0), (4, 1)),
            shape("boundary", (11, 0), (12, 1), "m2"),
            shape("boundary", (4, 0), (6, 1)),
            shape("boundary", (41, 0), (42, 1)),
            shape("boundary", (21, 0), (22, 1)),
        ]
        blockage_router.find_blockages("route", new_blockages)
        self.assertIs(blockage_router.blockages[0], m1_keep)
        self.assertIs(blockage_router.blockages[1], m2_keep)

        def blockage_signature(items):
            return [(item.layer, item.ll().x, item.by(),
                     item.rx(), item.uy()) for item in items]

        self.assertEqual(blockage_signature(blockage_router.blockages), [
            ("m1", 20, 0, 24, 1),
            ("m2", 30, 0, 31, 1),
            ("m1", 0, 0, 6, 1),
            ("m2", 10, 0, 14, 1),
        ])
        surviving_m2_route = blockage_router.blockages[-1]
        blockage_router.find_blockages(
            "route", [shape("boundary", (6, 0), (8, 1))])
        self.assertIs(blockage_router.blockages[0], m1_keep)
        self.assertIs(blockage_router.blockages[1], m2_keep)
        self.assertIs(blockage_router.blockages[2], surviving_m2_route)
        self.assertEqual(blockage_signature(blockage_router.blockages), [
            ("m1", 20, 0, 24, 1),
            ("m2", 30, 0, 31, 1),
            ("m2", 10, 0, 14, 1),
            ("m1", 0, 0, 8, 1),
        ])

        def shape_bounds(item):
            ll, ur = item.rect
            return (ll.x, ll.y, ur.x, ur.y)

        fast_router = router_class.__new__(router_class)
        merger_core = shape("merger_core", (0, 0), (2, 1))
        merger = graph_shape(
            "merger", [vector(-1, -1), vector(3, 2)], "m1", merger_core)
        aligned_core = shape("aligned_core", (2, 0), (4, 1))
        aligned_shape = graph_shape(
            "aligned", [vector(1, -1), vector(5, 2)],
            "m1", aligned_core)
        aligned_shapes = [aligned_shape]
        fast_router.merge_shapes(merger, aligned_shapes)
        self.assertEqual(aligned_shapes, [])
        self.assertEqual(shape_bounds(merger), (-1, -1, 5, 2))
        self.assertEqual(shape_bounds(merger_core), (0, 0, 4, 1))

        early = shape("early", (2, 0), (3, 1))
        bridge = shape("bridge", (1, 0), (2, 1))
        chain_merger = shape("chain_merger", (0, 0), (1, 1))
        chain = [early, bridge]
        fast_router.merge_shapes(chain_merger, chain)
        self.assertEqual([id(item) for item in chain], [id(early)])
        self.assertEqual(shape_bounds(chain_merger), (0, 0, 2, 1))

        early = shape("early_reverse", (2, 0), (3, 1))
        bridge = shape("bridge_reverse", (1, 0), (2, 1))
        chain_merger = shape("chain_merger_reverse", (0, 0), (1, 1))
        chain = [bridge, early]
        fast_router.merge_shapes(chain_merger, chain)
        self.assertEqual(chain, [])
        self.assertEqual(shape_bounds(chain_merger), (0, 0, 3, 1))

        cross_layer = shape("cross_layer", (0, 0), (1, 1), "m2")
        cross_layer_shapes = [cross_layer]
        fast_router.merge_shapes(
            shape("same_bounds_m1", (0, 0), (1, 1)),
            cross_layer_shapes)
        self.assertEqual([id(item) for item in cross_layer_shapes],
                         [id(cross_layer)])

        reversed_candidate = graph_shape(
            "reversed_candidate", [vector(0, 5), vector(4, 2)], "m1")
        reversed_shapes = [reversed_candidate]
        fast_router.merge_shapes(
            shape("reversed_merger", (0, 0), (4, 4)), reversed_shapes)
        self.assertEqual(reversed_shapes, [])


        contained_shape = shape("set_contained", (1, 1), (2, 2))
        contained_set = {contained_shape}
        fast_router.merge_shapes(
            shape("set_merger", (0, 0), (3, 3)), contained_set)
        self.assertEqual(contained_set, set())

        class dispatch_container(graph_shape):
            def contains(self, _other):
                self.contains_called = True
                return False

            def aligns(self, _other):
                self.aligns_called = True
                return True

        dispatch_merger = dispatch_container(
            "dispatch", [vector(0, 0), vector(1, 1)], "m1")
        dispatch_merger.contains_called = False
        dispatch_merger.aligns_called = False
        dispatch_shapes = [shape("dispatch_other", (4, 4), (5, 5))]
        fast_router.merge_shapes(dispatch_merger, dispatch_shapes)
        self.assertTrue(dispatch_merger.contains_called)
        self.assertTrue(dispatch_merger.aligns_called)
        self.assertEqual(dispatch_shapes, [])

        class core_container(graph_shape):
            def contains(self, _other):
                self.contains_called = True
                return True

        dynamic_container = core_container(
            "dynamic_container", [vector(10, 10), vector(11, 11)], "m1")
        dynamic_container.contains_called = False
        self.assertTrue(shape("dynamic_probe", (0, 0), (1, 1)).
                        core_contained_by_any([dynamic_container]))
        self.assertTrue(dynamic_container.contains_called)

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
        self.assertIsNotNone(bulk._flat_tree)
        self.assertIsNone(bulk.left._flat_tree)
        self.assertIsNone(bbox_node.build([]))
        self.assertIs(bbox_node.build([tree_boxes[0]]).bbox, tree_boxes[0])

        def result_ids(items):
            return sorted(id(item) for item in items)

        def leaf_shapes(node):
            if node.is_leaf:
                yield node.bbox.shape
                return
            if node.left:
                yield from leaf_shapes(node.left)
            if node.right:
                yield from leaf_shapes(node.right)

        ordered_shapes = list(leaf_shapes(bulk))

        def stack_results(method, *args):
            flat_tree = bulk._flat_tree
            bulk._flat_tree = None
            try:
                return list(method(*args))
            finally:
                bulk._flat_tree = flat_tree

        for point in [vector(-3, -1), vector(1, 1),
                      vector(2, 2), vector(5.5, 1), vector(10, 10)]:
            expected = []
            for item in ordered_shapes:
                ll, ur = item.rect
                if (ll.x <= point.x <= ur.x and
                        ll.y <= point.y <= ur.y):
                    expected.append(item)
            actual = list(bulk.iterate_point(point))
            self.assertEqual(
                [id(item) for item in actual],
                [id(item) for item in stack_results(
                    bulk.iterate_point, point)])
            self.assertEqual([id(item) for item in actual],
                             [id(item) for item in expected])
            self.assertEqual(result_ids(actual),
                             result_ids(incremental.iterate_point(point)))
        query_shapes = [
            shape("query_0", (1.5, 1.5), (2.5, 2.5)),
            shape("query_1", (-4, -2), (-3, -1)),
            shape("query_2", (8, 8), (9, 9)),
            shape("query_all", (-10, -10), (10, 10)),
        ]
        for query in query_shapes:
            qll, qur = query.rect
            expected = []
            for item in ordered_shapes:
                ll, ur = item.rect
                if (ll.x <= qur.x and qll.x <= ur.x and
                        ll.y <= qur.y and qll.y <= ur.y):
                    expected.append(item)
            actual = list(bulk.iterate_shape(query))
            self.assertEqual(
                [id(item) for item in actual],
                [id(item) for item in stack_results(
                    bulk.iterate_shape, query)])
            actual_bounds = list(bulk.iterate_rect(
                qll.x, qll.y, qur.x, qur.y))
            self.assertEqual([id(item) for item in actual_bounds],
                             [id(item) for item in actual])
            self.assertEqual([id(item) for item in actual],
                             [id(item) for item in expected])
            self.assertEqual(result_ids(actual),
                             result_ids(incremental.iterate_shape(query)))

        self.assertEqual(
            [id(item) for item in bulk.left.iterate_rect(-100, -100, 100, 100)],
            [id(item) for item in leaf_shapes(bulk.left)])

        none_bbox = router_bbox()
        none_bbox.rect = [vector(20, 20), vector(21, 21)]
        none_tree = bbox_node.build([
            none_bbox, router_bbox(shape("non_none", (22, 22), (23, 23)))])
        self.assertEqual(
            list(none_tree.iterate_point(vector(20.5, 20.5))), [None])

        leaf = bbox_node(tree_boxes[0])
        outside_point = vector(100, 100)
        outside_shape = shape("outside", (100, 100), (101, 101))
        self.assertEqual(list(leaf.iterate_point(outside_point)), [])
        self.assertEqual(list(leaf.iterate_shape(outside_shape)), [])
        self.assertEqual(list(leaf.iterate_point(outside_point, True)),
                         [tree_shapes[0]])
        self.assertEqual(list(leaf.iterate_shape(outside_shape, True)),
                         [tree_shapes[0]])
        self.assertEqual(
            list(leaf.iterate_rect(100, 100, 101, 101)), [])
        self.assertEqual(
            list(leaf.iterate_rect(100, 100, 101, 101, True)),
            [tree_shapes[0]])
        self.assertEqual(
            list(bulk.iterate_rect(100, 100, 101, 101, True)), [])
        self.assertEqual(list(bulk.iterate_point(outside_point, True)), [])
        self.assertEqual(list(bulk.iterate_shape(outside_shape, True)), [])

        inserted_after_build = shape("tree_inserted", (8, 0), (10, 2))
        bulk.insert(router_bbox(inserted_after_build))
        self.assertIsNone(bulk._flat_tree)
        self.assertEqual(list(bulk.iterate_point(vector(9, 1))),
                         [inserted_after_build])

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

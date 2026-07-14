# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California, Santa Cruz
# All rights reserved.
#
import heapq
from bisect import bisect_left
from copy import deepcopy
from openram import debug
from openram.base.vector import vector
from openram.tech import drc
from .bbox import bbox
from .bbox_node import bbox_node
from .graph_node import graph_node
from .graph_utils import snap


class graph:
    """ This is the graph created from the blockages. """

    def __init__(self, router):

        # This is the graph router that uses this graph
        self.router = router
        self.source_nodes = []
        self.target_nodes = []
        self._node_blockage_rules = None


    def is_routable(self, shape):
        """ Return if a shape is routable in this graph. """

        return shape.name == self.source.name


    def inside_shape(self, point, shape):
        """ Return if the point is inside the shape. """

        # Check if they're on the same layer
        if point.z != self.router.get_zindex(shape.lpp):
            return False
        # Check if the point is inside the shape
        ll, ur = shape.rect
        return shape.on_segment(ll, point, ur)


    def get_safe_pin_values(self, pin):
        """ Get the safe x and y values of the given pin. """

        # Constant values
        pin = pin.get_core()
        offset = self.router.half_wire
        spacing = self.router.track_space
        size_limit = snap(offset * 4 + spacing)

        x_values = []
        y_values = []
        # If one axis size of the pin is greater than the limit, we will take
        # two points at both ends. Otherwise, we will only take the center of
        # this pin.
        if pin.width() > size_limit:
            x_values.append(snap(pin.lx() + offset))
            x_values.append(snap(pin.rx() - offset))
        else:
            x_values.append(snap(pin.cx()))
        if pin.height() > size_limit:
            y_values.append(snap(pin.by() + offset))
            y_values.append(snap(pin.uy() - offset))
        else:
            y_values.append(snap(pin.cy()))

        return x_values, y_values


    def is_probe_blocked(self, p1, p2):
        """
        Return if a probe sent from p1 to p2 encounters a blockage.
        The probe must be sent vertically or horizontally.
        This function assumes that p1 and p2 are on the same layer.
        """

        p1x, p1y = p1.x, p1.y
        p2x, p2y = p2.x, p2.y
        pll_x = min(p1x, p2x)
        pll_y = min(p1y, p2y)
        pur_x = max(p1x, p2x)
        pur_y = max(p1y, p2y)
        probe_lpp = self.router.get_lpp(p1.z)
        blockage_tree = self.blockage_bbox_trees[p1.z]
        if blockage_tree is None:
            return False

        # Check if any blockage blocks this probe
        for blockage in blockage_tree.iterate_rect(
                pll_x, pll_y, pur_x, pur_y):
            # Not on the same layer
            if not blockage.same_lpp(blockage.lpp, probe_lpp):
                continue
            # Probe is blocked if the shape isn't routable
            if not self.is_routable(blockage):
                return True
            blockage = blockage.get_core()
            bll, bur = blockage.rect
            # Not overlapping
            if (bll.x > pur_x or pll_x > bur.x or
                    bll.y > pur_y or pll_y > bur.y):
                return True
        return False


    def is_node_blocked(self, node):
        """ Return if a node is blocked by a blockage. """

        p = node.center
        x = p.x
        y = p.y
        z = p.z
        blockage_tree = self.blockage_bbox_trees[z]
        if blockage_tree is None:
            return False
        rules = self._node_blockage_rules
        if rules is None:
            wide = self.router.track_wire
            half_wide = self.router.half_wire
            spacing = snap(self.router.track_space + half_wide + drc["grid"])
            rules = (wide, half_wide, spacing)
            self._node_blockage_rules = rules
        else:
            wide, half_wide, spacing = rules

        def closest(value, checklist):
            """ Return the distance of the closest value in the checklist. """
            diffs = [abs(value - other) for other in checklist]
            return snap(min(diffs))

        blocked = False
        for blockage in blockage_tree.iterate_point(p):
            # Not on the same layer
            if self.router.get_zindex(blockage.lpp) != z:
                continue
            # Blocked if not routable
            if not self.is_routable(blockage):
                blocked = True
                continue
            blockage = blockage.get_core()
            ll, ur = blockage.rect
            # Not overlapping
            if ll.x > x or x > ur.x or ll.y > y or y > ur.y:
                blocked = True
                continue
            # Check if the node is too close to one edge of the shape
            lengths = [blockage.width(), blockage.height()]
            centers = blockage.center()
            ll, ur = blockage.rect
            safe = [True, True]
            for i in range(2):
                if lengths[i] >= wide:
                    min_diff = closest(p[i], [ll[i], ur[i]])
                    if min_diff < half_wide:
                        safe[i] = False
                elif centers[i] != p[i]:
                    safe[i] = False
            if not all(safe):
                blocked = True
                continue
            # Check if the node is in a safe region of the shape
            xs, ys = self.get_safe_pin_values(blockage)
            xdiff = closest(p.x, xs)
            ydiff = closest(p.y, ys)
            if xdiff == 0 and ydiff == 0:
                if blockage in [self.source, self.target]:
                    return False
            elif xdiff < spacing and ydiff < spacing:
                blocked = True
        return blocked


    def is_via_blocked(self, nodes, check_blockages=True):
        """ Return if a via on the given point is blocked. """

        # If the nodes are blocked by a blockage other than a via
        if check_blockages:
            for node in nodes:
                if self.is_node_blocked(node):
                    return True

        # Skip if no via is present
        if len(self.graph_vias) == 0:
            return False

        # If the nodes are blocked by a via
        if not check_blockages:
            node = nodes[-1]
        x = node.center.x
        y = node.center.y
        z = node.center.z
        for via in self.via_bbox_tree.iterate_point(node.center):
            ll, ur = via.rect
            # Not overlapping
            if ll.x > x or x > ur.x or ll.y > y or y > ur.y:
                continue
            center = via.center()
            # If not in the center
            if center.x != x or center.y != y:
                return True
        return False


    def create_graph(self, source, target):
        """ Create the graph to run routing on later. """
        debug.info(3, "Creating the graph for source '{}' and target'{}'.".format(source, target))

        # Save source and target information
        self.source = source
        self.target = target

        # Find the region to be routed and only include objects inside that region
        region = deepcopy(source)
        region.bbox([target])
        region = region.inflated_pin(spacing=self.router.track_width + self.router.track_space)
        debug.info(4, "Routing region is {}".format(region.rect))

        # Find the blockages that are in the routing area
        self.graph_blockages = []
        self.graph_blockage_set = set()
        self.find_graph_blockages(region)

        # Find the vias that are in the routing area
        self.graph_vias = []
        self.find_graph_vias(region)

        # Generate the cartesian values from shapes in the area
        x_values, y_values = self.generate_cartesian_values()
        # Adjust the routing region to include "edge" shapes
        region.bbox(self.graph_blockages)
        # Find and include edge shapes to prevent DRC errors
        self.find_graph_blockages(region)
        # Build the bbox tree
        self.build_bbox_trees()
        # Generate the graph nodes from cartesian values
        self.generate_graph_nodes(x_values, y_values)
        # Save the graph nodes that lie in source and target shapes
        self.save_end_nodes()
        debug.info(4, "Number of blockages detected in the routing region: {}".format(len(self.graph_blockages)))
        debug.info(4, "Number of vias detected in the routing region: {}".format(len(self.graph_vias)))
        debug.info(4, "Number of nodes in the routing graph: {}".format(len(self.nodes)))


    def find_graph_blockages(self, region):
        """ Find blockages that overlap the routing region. """

        for blockage in self.router.blockages:
            # Skip if already included
            if blockage in self.graph_blockage_set:
                continue
            # Set the region's lpp to current blockage's lpp so that the
            # overlaps method works
            region.lpp = blockage.lpp
            if region.overlaps(blockage):
                self.graph_blockages.append(blockage)
                self.graph_blockage_set.add(blockage)
        # Make sure that the source or target fake pins are included as blockage
        for shape in [self.source, self.target]:
            for blockage in self.graph_blockages:
                blockage = blockage.get_core()
                if shape == blockage:
                    break
            else:
                self.graph_blockages.append(shape)
                self.graph_blockage_set.add(shape)


    def find_graph_vias(self, region):
        """ Find vias that overlap the routing region. """

        for via in self.router.vias:
            # Skip if already included
            if via in self.graph_vias:
                continue
            # Set the regions's lpp to current via's lpp so that the
            # overlaps method works
            region.lpp = via.lpp
            if region.overlaps(via):
                self.graph_vias.append(via)


    def build_bbox_trees(self):
        """ Build bbox trees for blockages and vias in the routing region. """

        route_lpps = [self.router.get_lpp(z) for z in range(2)]
        blockage_boxes = [[], []]
        for shape in self.graph_blockages:
            shape_z = self.router.get_zindex(shape.lpp)
            for z, route_lpp in enumerate(route_lpps):
                if (shape_z == z or
                        self.router.same_lpp(shape.lpp, route_lpp)):
                    blockage_boxes[z].append(bbox(shape))
        self.blockage_bbox_trees = [bbox_node.build(boxes)
                                    for boxes in blockage_boxes]
        if self.graph_vias:
            via_boxes = [bbox(shape) for shape in self.graph_vias]
            self.via_bbox_tree = bbox_node.build(via_boxes)


    def generate_cartesian_values(self):
        """
        Generate x and y values from all the corners of the shapes in the
        routing region.
        """

        x_values = set()
        y_values = set()

        # Add inner values for blockages of the routed type
        for shape in self.graph_blockages:
            if not self.is_routable(shape):
                continue
            # Get the safe pin values
            xs, ys = self.get_safe_pin_values(shape)
            x_values.update(xs)
            y_values.update(ys)

        # Add corners for blockages
        offset = vector([drc["grid"]] * 2)
        for blockage in self.graph_blockages:
            ll, ur = blockage.rect
            # Add minimum offset to the blockage corner nodes to prevent overlap
            nll = snap(ll - offset)
            nur = snap(ur + offset)
            x_values.update([nll.x, nur.x])
            y_values.update([nll.y, nur.y])

        # Add center values for existing vias
        for via in self.graph_vias:
            p = via.center()
            x_values.add(p.x)
            y_values.add(p.y)

        # Sort x and y values
        x_values = list(x_values)
        y_values = list(y_values)
        x_values.sort()
        y_values.sort()

        return x_values, y_values


    def generate_graph_nodes(self, x_values, y_values):
        """
        Generate all graph nodes using the cartesian values and connect the
        orthogonal neighbors.
        """

        # Generate all nodes
        self.nodes = []
        for x in x_values:
            for y in y_values:
                for z in [0, 1]:
                    self.nodes.append(graph_node([x, y, z]))

        # Mark nodes that will be removed
        self.mark_blocked_nodes()

        # Connect the nearest live nodes without rescanning prior grid points.
        nodes = self.nodes
        y_len = len(y_values)
        previous_x = [[None, None] for _ in range(y_len)]
        previous_x_positions = [[-1, -1] for _ in range(y_len)]
        for x_index in range(len(x_values)):
            previous_y = [None, None]
            previous_y_positions = [-1, -1]
            x_offset = x_index * y_len * 2
            for y_index in range(y_len):
                i = x_offset + y_index * 2
                base0 = nodes[i]
                base1 = nodes[i + 1]

                down0 = previous_y[0] if not base0.remove else None
                down1 = previous_y[1] if not base1.remove else None
                if previous_y_positions[0] >= previous_y_positions[1]:
                    if (down0 is not None and
                            not self.is_probe_blocked(
                                base0.center, down0.center)):
                        base0.add_neighbor(down0)
                    if (down1 is not None and
                            not self.is_probe_blocked(
                                base1.center, down1.center)):
                        base1.add_neighbor(down1)
                else:
                    if (down1 is not None and
                            not self.is_probe_blocked(
                                base1.center, down1.center)):
                        base1.add_neighbor(down1)
                    if (down0 is not None and
                            not self.is_probe_blocked(
                                base0.center, down0.center)):
                        base0.add_neighbor(down0)

                left0 = (previous_x[y_index][0]
                         if not base0.remove else None)
                left1 = (previous_x[y_index][1]
                         if not base1.remove else None)
                positions = previous_x_positions[y_index]
                if positions[0] >= positions[1]:
                    if (left0 is not None and
                            not self.is_probe_blocked(
                                base0.center, left0.center)):
                        base0.add_neighbor(left0)
                    if (left1 is not None and
                            not self.is_probe_blocked(
                                base1.center, left1.center)):
                        base1.add_neighbor(left1)
                else:
                    if (left1 is not None and
                            not self.is_probe_blocked(
                                base1.center, left1.center)):
                        base1.add_neighbor(left1)
                    if (left0 is not None and
                            not self.is_probe_blocked(
                                base0.center, left0.center)):
                        base0.add_neighbor(left0)

                if not base0.remove:
                    previous_y[0] = base0
                    previous_y_positions[0] = y_index
                    previous_x[y_index][0] = base0
                    positions[0] = x_index
                if not base1.remove:
                    previous_y[1] = base1
                    previous_y_positions[1] = y_index
                    previous_x[y_index][1] = base1
                    positions[1] = x_index

                if (not base0.remove and not base1.remove and
                        not self.is_via_blocked(
                            (base0, base1), check_blockages=False)):
                    base0.add_neighbor(base1)

        # Remove marked nodes
        self.remove_blocked_nodes()


    def mark_blocked_nodes(self):
        """ Mark graph nodes to be removed that are blocked by a blockage. """

        is_blocked = self.is_node_blocked
        for i in range(len(self.nodes) - 1, -1, -1):
            node = self.nodes[i]
            if is_blocked(node):
                node.remove = True


    def remove_blocked_nodes(self):
        """ Remove graph nodes that are marked to be removed. """

        kept = 0
        for node in self.nodes:
            if node.remove:
                node.remove_all_neighbors()
            else:
                self.nodes[kept] = node
                kept += 1
        del self.nodes[kept:]


    def save_end_nodes(self):
        """ Save graph nodes that are inside source and target pins. """

        for node in self.nodes:
            if self.inside_shape(node.center, self.source):
                self.source_nodes.append(node)
            elif self.inside_shape(node.center, self.target):
                self.target_nodes.append(node)


    @staticmethod
    def _make_heuristic(target_nodes):
        """ Return a cached Manhattan-distance heuristic. """

        target_values = [(node.center.x, node.center.y, node.center.z)
                         for node in target_nodes]
        distances = {}
        target_set = set(target_values)
        x_values = sorted({x for x, _y, _z in target_set})
        y_values = sorted({y for _x, y, _z in target_set})
        z_values = sorted({z for _x, _y, z in target_set})
        is_cartesian = (bool(target_values) and
                        len(target_set) ==
                        len(x_values) * len(y_values) * len(z_values))

        if len(target_set) == 1:
            target_x, target_y, target_z = next(iter(target_set))

            def distance_to_targets(center):
                return (abs(target_x - center.x) +
                        abs(target_y - center.y) +
                        abs(target_z - center.z))

        elif is_cartesian:
            def nearest_axis_distance(value, values):
                index = bisect_left(values, value)
                if index == 0:
                    return abs(values[0] - value)
                if index == len(values):
                    return abs(values[-1] - value)
                return min(abs(values[index - 1] - value),
                           abs(values[index] - value))

            def distance_to_targets(center):
                return (nearest_axis_distance(center.x, x_values) +
                        nearest_axis_distance(center.y, y_values) +
                        nearest_axis_distance(center.z, z_values))

        else:
            def distance_to_targets(center):
                min_dist = float("inf")
                for x, y, z in target_values:
                    dist = (abs(x - center.x) + abs(y - center.y) +
                            abs(z - center.z))
                    if dist < min_dist:
                        min_dist = dist
                return min_dist

        def h(node):
            cached = distances.get(node.id)
            if cached is not None:
                return cached
            min_dist = distance_to_targets(node.center)
            distances[node.id] = min_dist
            return min_dist

        return h


    def find_shortest_path(self):
        """
        Find the shortest path from the source node to target node using the
        A* algorithm.
        """

        h = self._make_heuristic(self.target_nodes)

        # Initialize data structures to be used for A* search
        queue = []
        close_set = set()
        came_from = {}
        g_scores = {}
        f_scores = {}

        # Initialize score values for the source nodes
        for node in self.source_nodes:
            g_scores[node.id] = 0
            f_scores[node.id] = h(node)
            heapq.heappush(queue, (f_scores[node.id], node.id, node))

        # Run the A* algorithm
        while len(queue) > 0:
            # Get the closest node from the queue
            current = heapq.heappop(queue)[2]

            # Skip this node if already discovered
            if current in close_set:
                continue
            close_set.add(current)

            # Check if we've reached the target
            if current in self.target_nodes:
                path = []
                while current.id in came_from:
                    path.append(current)
                    current = came_from[current.id]
                path.append(current)
                path.reverse()
                return path

            # Get the previous node to better calculate the next costs
            prev_node = None
            if current.id in came_from:
                prev_node = came_from[current.id]

            # Update neighbor scores
            for node in current.neighbors:
                tentative_score = current.get_edge_cost(node, prev_node) + g_scores[current.id]
                if node.id not in g_scores or tentative_score < g_scores[node.id]:
                    came_from[node.id] = current
                    g_scores[node.id] = tentative_score
                    f_scores[node.id] = tentative_score + h(node)
                    heapq.heappush(queue, (f_scores[node.id], node.id, node))

        # Return None if not connected
        return None

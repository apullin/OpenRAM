# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California, Santa Cruz
# All rights reserved.
#

from operator import itemgetter

class bbox_node:
    """
    This class represents a node in the bbox tree structure. Bbox trees are
    binary trees we use to partition the shapes in the routing region so that
    we can detect overlaps faster in a binary search-like manner.
    """

    def __init__(self, bbox, left=None, right=None):

        self.bbox = bbox
        self.is_leaf = not left and not right
        self.left = left
        self.right = right
        self._flat_tree = None


    def iterate_point(self, point, check_done=False):
        """ Iterate over shapes in the tree that overlap the given point. """

        px, py = point.x, point.y
        if self.is_leaf:
            ll, ur = self.bbox.rect
            if check_done or (ll.x <= px <= ur.x and ll.y <= py <= ur.y):
                yield self.bbox.shape
            return

        flat_tree = self._flat_tree
        if flat_tree is not None:
            index = 1
            end = len(flat_tree)
            while index < end:
                llx, lly, urx, ury, escape, leaf_bbox = flat_tree[index]
                if llx <= px <= urx and lly <= py <= ury:
                    index += 1
                    if leaf_bbox is not None:
                        yield leaf_bbox.shape
                else:
                    index = escape
            return

        stack = []
        if self.right:
            stack.append(self.right)
        if self.left:
            stack.append(self.left)
        while stack:
            node = stack.pop()
            ll, ur = node.bbox.rect
            if not (ll.x <= px <= ur.x and ll.y <= py <= ur.y):
                continue
            if node.is_leaf:
                yield node.bbox.shape
                continue
            if node.right:
                stack.append(node.right)
            if node.left:
                stack.append(node.left)


    def iterate_shape(self, shape, check_done=False):
        """ Iterate over shapes in the tree that overlap the given shape. """

        sll, sur = shape.rect
        yield from self.iterate_rect(sll.x, sll.y, sur.x, sur.y,
                                     check_done)


    def iterate_rect(self, sllx, slly, surx, sury, check_done=False):
        """Iterate over shapes that overlap the given rectangle bounds."""

        if self.is_leaf:
            ll, ur = self.bbox.rect
            if check_done or (ll.x <= surx and sllx <= ur.x and
                              ll.y <= sury and slly <= ur.y):
                yield self.bbox.shape
            return

        flat_tree = self._flat_tree
        if flat_tree is not None:
            index = 1
            end = len(flat_tree)
            while index < end:
                llx, lly, urx, ury, escape, leaf_bbox = flat_tree[index]
                if (llx <= surx and sllx <= urx and
                        lly <= sury and slly <= ury):
                    index += 1
                    if leaf_bbox is not None:
                        yield leaf_bbox.shape
                else:
                    index = escape
            return

        stack = []
        if self.right:
            stack.append(self.right)
        if self.left:
            stack.append(self.left)
        while stack:
            node = stack.pop()
            ll, ur = node.bbox.rect
            if not (ll.x <= surx and sllx <= ur.x and
                    ll.y <= sury and slly <= ur.y):
                continue
            if node.is_leaf:
                yield node.bbox.shape
                continue
            if node.right:
                stack.append(node.right)
            if node.left:
                stack.append(node.left)


    @classmethod
    def build(cls, boxes):
        """Build a deterministic, balanced bbox tree."""

        if not boxes:
            return None
        indexed = []
        for index, box in enumerate(boxes):
            ll, ur = box.rect
            indexed.append((index, box,
                            ll.x + ur.x, ll.y + ur.y,
                            ll.x, ll.y, ur.x, ur.y))

        def build_items(items):
            if len(items) == 1:
                return cls(items[0][1])

            x_centers = [item[2] for item in items]
            y_centers = [item[3] for item in items]
            x_span = max(x_centers) - min(x_centers)
            y_span = max(y_centers) - min(y_centers)
            if y_span > x_span:
                sort_key = itemgetter(3, 2, 4, 5, 6, 7, 0)
            else:
                sort_key = itemgetter(2, 3, 4, 5, 6, 7, 0)

            ordered = sorted(items, key=sort_key)
            middle = len(ordered) // 2
            left = build_items(ordered[:middle])
            right = build_items(ordered[middle:])
            return cls(left.bbox.merge(right.bbox), left, right)

        root = build_items(indexed)
        flat_tree = []

        def flatten(node):
            index = len(flat_tree)
            flat_tree.append(None)
            if not node.is_leaf:
                if node.left:
                    flatten(node.left)
                if node.right:
                    flatten(node.right)
            ll, ur = node.bbox.rect
            flat_tree[index] = (ll.x, ll.y, ur.x, ur.y,
                                len(flat_tree),
                                node.bbox if node.is_leaf else None)

        flatten(root)
        root._flat_tree = flat_tree
        return root


    def get_costs(self, bbox):
        """ Return the costs of bbox nodes after merging the given bbox. """

        # Find the new areas for all possible cases
        self_merge = bbox.merge(self.bbox)
        left_merge = bbox.merge(self.left.bbox)
        right_merge = bbox.merge(self.right.bbox)
        self_merge_area = self_merge.area()
        self_bbox_area = self.bbox.area()

        # Add the change in areas as cost
        self_cost = self_merge_area
        left_cost = self_merge_area - self_bbox_area
        left_cost += left_merge.area() - self.left.bbox.area()
        right_cost = self_merge_area - self_bbox_area
        right_cost += right_merge.area() - self.right.bbox.area()

        # Add the overlaps in areas as cost
        self_overlap = self.bbox.overlap(bbox)
        left_overlap = left_merge.overlap(self.right.bbox)
        right_overlap = right_merge.overlap(self.left.bbox)
        if self_overlap:
            self_cost += self_overlap.area()
        if left_overlap:
            left_cost += left_overlap.area()
        if right_overlap:
            right_cost += right_overlap.area()

        return self_cost, left_cost, right_cost


    def insert(self, bbox):
        """ Insert a bbox to the bbox tree. """

        self._flat_tree = None

        if self.is_leaf:
            # Put the current bbox to the left child
            self.left = bbox_node(self.bbox)
            # Put the new bbox to the right child
            self.right = bbox_node(bbox)
        else:
            # Calculate the costs of adding the new bbox
            self_cost, left_cost, right_cost = self.get_costs(bbox)
            if self_cost < left_cost and self_cost < right_cost: # Add here
                self.left = bbox_node(self.bbox, left=self.left, right=self.right)
                self.right = bbox_node(bbox)
            elif left_cost < right_cost: # Add to the left
                self.left.insert(bbox)
            else: # Add to the right
                self.right.insert(bbox)
        # Update the current bbox
        self.bbox = self.left.bbox.merge(self.right.bbox)
        self.is_leaf = False

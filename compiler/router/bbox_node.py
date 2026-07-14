# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California, Santa Cruz
# All rights reserved.
#

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


    def iterate_point(self, point, check_done=False):
        """ Iterate over shapes in the tree that overlap the given point. """

        px, py = point.x, point.y
        if self.is_leaf:
            ll, ur = self.bbox.rect
            if check_done or (ll.x <= px <= ur.x and ll.y <= py <= ur.y):
                yield self.bbox.shape
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
        if self.is_leaf:
            ll, ur = self.bbox.rect
            if check_done or (ll.x <= sur.x and sll.x <= ur.x and
                              ll.y <= sur.y and sll.y <= ur.y):
                yield self.bbox.shape
            return

        stack = []
        if self.right:
            stack.append(self.right)
        if self.left:
            stack.append(self.left)
        while stack:
            node = stack.pop()
            ll, ur = node.bbox.rect
            if not (ll.x <= sur.x and sll.x <= ur.x and
                    ll.y <= sur.y and sll.y <= ur.y):
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
        indexed = list(enumerate(boxes))

        def centroid(item, axis):
            _index, box = item
            ll, ur = box.rect
            return ll[axis] + ur[axis]

        def build_items(items):
            if len(items) == 1:
                return cls(items[0][1])

            x_centers = [centroid(item, 0) for item in items]
            y_centers = [centroid(item, 1) for item in items]
            x_span = max(x_centers) - min(x_centers)
            y_span = max(y_centers) - min(y_centers)
            axis = int(y_span > x_span)
            other_axis = 1 - axis

            def sort_key(item):
                index, box = item
                ll, ur = box.rect
                return (centroid(item, axis),
                        centroid(item, other_axis),
                        ll.x, ll.y, ur.x, ur.y, index)

            ordered = sorted(items, key=sort_key)
            middle = len(ordered) // 2
            left = build_items(ordered[:middle])
            right = build_items(ordered[middle:])
            return cls(left.bbox.merge(right.bbox), left, right)

        return build_items(indexed)


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

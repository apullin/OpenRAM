# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California, Santa Cruz
# All rights reserved.
#
"""
Utility functions for graph router.
"""
from openram.base import vector
from openram import tech

_snap_grid = object()
_snap_precision = 0


def snap(a):
    """ Use custom `snap` since `vector.snap_to_grid` isn't working. """

    global _snap_grid, _snap_precision

    if isinstance(a, vector):
        return vector(snap(a.x), snap(a.y))
    grid = tech.drc["grid"]
    if grid != _snap_grid:
        _snap_grid = grid
        _snap_precision = len(str(grid).split('.')[1])
    return round(a, _snap_precision)

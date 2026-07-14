# See LICENSE for licensing information.
#
# Copyright (c) 2016-2026 Regents of the University of California, Santa Cruz
# All rights reserved.
#
"""
Rust backend for the Hanan graph router. Mirrors the `graph` class API used
by supply_router and signal_escape_router, delegating create_graph and
find_shortest_path to the openram_rs extension (rust/openram-py).
"""
import os
from openram.base.vector3d import vector3d
from openram.tech import drc

_rs = None


def load_openram_rs():
    """Import openram_rs, falling back to the in-repo cargo build artifact."""
    global _rs
    if _rs is not None:
        return _rs
    try:
        import openram_rs
        _rs = openram_rs
        return _rs
    except ImportError:
        pass
    home = os.environ.get("OPENRAM_HOME")
    if not home:
        return None
    so_path = os.path.join(home, "..", "rust", "target", "release",
                           "libopenram_rs.so")
    if not os.path.exists(so_path):
        return None
    from importlib.machinery import ExtensionFileLoader
    from importlib.util import spec_from_loader, module_from_spec
    loader = ExtensionFileLoader("openram_rs", so_path)
    spec = spec_from_loader("openram_rs", loader)
    module = module_from_spec(spec)
    loader.exec_module(module)
    _rs = module
    return _rs


class path_node:
    """ Lightweight stand-in for graph_node on a returned path. """

    __slots__ = ("center",)

    def __init__(self, x, y, z):
        self.center = vector3d(x, y, z)

    def get_direction(self, b):
        return (self.center.x == b.center.x, self.center.y == b.center.y)


class rust_graph:
    """ Drop-in for `graph` backed by the Rust kernel. """

    def __init__(self, router):
        self.router = router
        # Populated for write_debug_gds compatibility on failures.
        self.graph_blockages = []
        self.nodes = []
        self._names = {}
        self._lpps = {}

        grid = drc["grid"]
        ndigits = len(str(grid).split('.')[1])
        self._ndigits = ndigits
        rs = load_openram_rs()
        self._router_rs = rs.Router(
            grid,
            ndigits,
            router.track_wire,
            router.track_space,
            router.half_wire,
            router.track_width + router.track_space,
        )
        self._route_lpps = [router.get_lpp(0), router.get_lpp(1)]

    def _name_id(self, name):
        return self._names.setdefault(str(name), len(self._names))

    def _lpp_id(self, lpp):
        return self._lpps.setdefault(repr(lpp), len(self._lpps))

    @staticmethod
    def _same_lpp(lpp1, lpp2):
        if lpp1 is lpp2:
            return True
        if lpp1[1] is None or lpp2[1] is None:
            return lpp1[0] == lpp2[0]
        return lpp1[0] == lpp2[0] and lpp1[1] == lpp2[1]

    def _convert(self, shape):
        ll, ur = shape.rect
        core = shape.get_core()
        cll, cur = core.rect
        lpp = shape.lpp
        return ((ll.x, ll.y, ur.x, ur.y, cll.x, cll.y, cur.x, cur.y),
                (self._name_id(shape.name),
                 self._lpp_id(lpp),
                 self.router.get_zindex(lpp),
                 self._same_lpp(lpp, self._route_lpps[0]),
                 self._same_lpp(lpp, self._route_lpps[1])))

    def create_graph(self, source, target):
        """ Stage the inputs; the Rust side runs both phases in route(). """
        self._source = self._convert(source)
        self._target = self._convert(target)
        self._router_rs.set_blockages(
            [self._convert(s) for s in self.router.blockages])
        self._router_rs.set_vias(
            [self._convert(s) for s in self.router.vias])

    def find_shortest_path(self):
        path = self._router_rs.route(self._source, self._target)
        if path is None:
            return None
        return [path_node(x, y, z) for (x, y, z) in path]

# See LICENSE for licensing information.
#
# Copyright (c) 2016-2026 Regents of the University of California, Santa Cruz
# All rights reserved.
#
"""
Store-backed route() implementations: the Rust RouterStore owns the
blockage/via lists (find_blockages merging, via dedup, overlap naming)
and routes each pair directly from them, eliminating the per-pair
reconversion of every blockage. Pin finding, DRC inflation, side/ring
pin creation, MST pairing, and all design mutations stay in Python and
mirror supply_router.route / signal_escape_router.route line for line.
"""
from openram import debug
from openram import OPTS
from openram.tech import drc
from .graph_shape import graph_shape
from .rust_router import load_openram_rs
from .rust_router import path_node


class store_context:
    """ One RouterStore plus the shape conversion/interning for it. """

    def __init__(self, router):
        rs = load_openram_rs()
        self.router = router
        grid = drc["grid"]
        ndigits = len(str(grid).split('.')[1])
        self.store = rs.RouterStore(
            grid,
            ndigits,
            router.track_wire,
            router.track_space,
            router.half_wire,
            router.track_width + router.track_space,
        )
        self._names = {}
        self._lpps = {}
        self._route_lpps = [router.get_lpp(0), router.get_lpp(1)]

    def _name_id(self, name):
        name = str(name)
        try:
            return self._names[name]
        except KeyError:
            nid = self.store.intern_name(name)
            self._names[name] = nid
            return nid

    def _lpp_id(self, lpp):
        key = repr(lpp)
        try:
            return self._lpps[key]
        except KeyError:
            lid = self.store.intern_lpp(key)
            self._lpps[key] = lid
            return lid

    @staticmethod
    def _same_lpp(lpp1, lpp2):
        if lpp1 is lpp2:
            return True
        if lpp1[1] is None or lpp2[1] is None:
            return lpp1[0] == lpp2[0]
        return lpp1[0] == lpp2[0] and lpp1[1] == lpp2[1]

    def convert(self, shape):
        ll, ur = shape.rect
        core = shape.get_core()
        cll, cur = core.rect
        lpp = shape.lpp
        purpose = -1 if lpp[1] is None else lpp[1]
        return ((ll.x, ll.y, ur.x, ur.y, cll.x, cll.y, cur.x, cur.y),
                (self._name_id(shape.name),
                 self._lpp_id(lpp),
                 self.router.get_zindex(lpp),
                 self._same_lpp(lpp, self._route_lpps[0]),
                 self._same_lpp(lpp, self._route_lpps[1]),
                 lpp[0],
                 purpose))

    # --- mirrors of the router's find/convert methods ------------------

    def add_all_pins(self):
        for pin in self.router.iter_pins(self.router.all_pins):
            self.store.add_pin(self.convert(pin))

    def find_blockages(self, name="blockage", shape_list=None):
        """ Mirror of router.find_blockages with the merge in Rust. """
        router = self.router
        from openram.base.vector import vector
        for lpp in [router.vert_lpp, router.horiz_lpp]:
            batch = []
            if shape_list is None:
                shapes = router.layout.getAllShapes(lpp)
                for boundary in shapes:
                    ll = vector(boundary[0], boundary[1])
                    ur = vector(boundary[2], boundary[3])
                    new_shape = graph_shape(name, [ll, ur], lpp)
                    batch.append(self.convert(router.inflate_shape(new_shape)))
            else:
                for boundary in shape_list:
                    if boundary.lpp != lpp:
                        continue
                    new_shape = graph_shape(name,
                                            [boundary.ll(), boundary.ur()],
                                            lpp)
                    batch.append(self.convert(router.inflate_shape(new_shape)))
            self.store.find_blockages_layer(lpp[0], batch)

    def find_vias(self, shape_list=None):
        """ Mirror of router.find_vias with the dedup in Rust. """
        router = self.router
        from openram.tech import layer
        from openram.base.vector import vector
        via_lpp = layer[router.via_layer_name]
        valid_lpp = router.horiz_lpp

        if shape_list is None:
            shapes = router.layout.getAllShapes(via_lpp)
        else:
            shapes = shape_list
        pairs = []
        for boundary in shapes:
            if shape_list is not None:
                ll = boundary.ll()
                ur = boundary.ur()
            else:
                ll = vector(boundary[0], boundary[1])
                ur = vector(boundary[2], boundary[3])
            new_shape = graph_shape("via", [ll, ur], valid_lpp)
            pairs.append((self.convert(new_shape),
                          self.convert(router.inflate_shape(new_shape))))
        self.store.find_vias(pairs)

    def route(self, source, target):
        path = self.store.route(self.convert(source), self.convert(target))
        if path is None:
            return None
        return [path_node(x, y, z) for (x, y, z) in path]


def supply_route(self, vdd_name="vdd", gnd_name="gnd"):
    """ Store-backed mirror of supply_router.route. """
    debug.info(1, "Running router for {} and {}...".format(vdd_name, gnd_name))

    self.vdd_name = vdd_name
    self.gnd_name = gnd_name

    self.prepare_gds_reader()
    self.find_pins(vdd_name)
    self.find_pins(gnd_name)

    ctx = store_context(self)
    ctx.add_all_pins()
    ctx.find_blockages()
    ctx.find_vias()
    ctx.store.convert_vias()
    ctx.store.convert_blockages()

    if self.pin_type in ["top", "bottom", "right", "left"]:
        for pin_name in [vdd_name, gnd_name]:
            new_shape, fake_pins = self.add_side_pin(pin_name,
                                                     self.pin_type)
            ll, ur = new_shape.rect
            layer = self.get_layer(self.pin_type in ["left", "right"])
            new_pin = graph_shape(name=pin_name,
                                  rect=[ll, ur],
                                  layer_name_pp=layer)

            # Match the Python router: publish the exported rail, add fake
            # MST targets along it, and keep the rail itself out of routing.
            self.new_pins[pin_name] = [new_pin]
            self.pins[pin_name].update(fake_pins)
            self.fake_pins.extend(fake_pins)
            blockage = self.inflate_shape(new_pin)
            ctx.store.append_blockage(ctx.convert(blockage))
    elif self.pin_type == "ring":
        sink = lambda s: ctx.store.append_blockage(ctx.convert(s))
        self.add_ring_pin(vdd_name, blockage_sink=sink)
        self.add_ring_pin(gnd_name, blockage_sink=sink)
    else:
        debug.warning("Side supply pins aren't created.")

    # Add vdd and gnd pins as blockages as well
    for pin in self.iter_pins(self.all_pins):
        ctx.store.append_blockage(ctx.convert(self.inflate_shape(pin)))

    routed_count = 0
    routed_max = len(self.pins[vdd_name]) + len(self.pins[gnd_name])
    for pin_name in [vdd_name, gnd_name]:
        pins = self.iter_pins(self.pins[pin_name])
        for source, target in self.get_mst_pairs(list(pins)):
            path = ctx.route(source, target)
            if path is None:
                self.write_debug_gds(gds_name="{}error.gds".format(OPTS.openram_temp),
                                     g=None, source=source, target=target)
                debug.error("Couldn't route from {} to {}.".format(source, target), -1)
            new_wires, new_vias = self.add_path(path)
            ctx.find_blockages(pin_name, new_wires)
            ctx.find_vias(new_vias)
            routed_count += 1
            debug.info(2, "Routed {} of {} supply pins".format(routed_count, routed_max))


def escape_route(self, pin_names):
    """ Store-backed mirror of signal_escape_router.route. """
    debug.info(1, "Running signal escape router...")

    self.prepare_gds_reader()
    for name in pin_names:
        self.find_pins(name)
    # NOTE: the reference implementation passes the leaked loop variable
    # `name` (the last pin name) to find_blockages for every route below;
    # keep that behavior since blockage names decide routability.
    leaked_name = name

    ctx = store_context(self)
    ctx.add_all_pins()
    ctx.find_blockages()
    ctx.find_vias()
    ctx.store.convert_vias()
    ctx.store.convert_blockages()

    self.add_perimeter_fake_pins()

    for pin in self.iter_pins(self.all_pins):
        ctx.store.append_blockage(ctx.convert(self.inflate_shape(pin)))

    routed_count = 0
    routed_max = len(pin_names)
    for source, target, _ in self.get_route_pairs(pin_names):
        target.name = source.name
        path = ctx.route(source, target)
        if path is None:
            self.write_debug_gds(gds_name="{}error.gds".format(OPTS.openram_temp),
                                 g=None, source=source, target=target)
            debug.error("Couldn't route from {} to {}.".format(source, target), -1)
        new_wires, new_vias = self.add_path(path)
        self.new_pins[source.name] = new_wires[-1]
        ctx.find_blockages(leaked_name, new_wires)
        ctx.find_vias(new_vias)
        routed_count += 1
        debug.info(2, "Routed {} of {} signal pins".format(routed_count, routed_max))
    self.replace_layout_pins()

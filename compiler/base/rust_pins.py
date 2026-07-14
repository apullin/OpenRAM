# See LICENSE for licensing information.
#
# Copyright (c) 2016-2026 Regents of the University of California, Santa Cruz
# All rights reserved.
#
"""
Rust-backed pin groups for hierarchy_layout.pin_map.

copy_layout_pin over big bitcell arrays dominates layout construction: per
instance it builds transformed pin_layout copies just to record a snapped
rectangle in pin_map. The fast path here keeps whole name-groups of pins in
a Rust PinStore (one per design module) — transform, snap, and dedup all
happen in Rust with the exact float semantics of pin_layout/vector — and
pin_map holds a lightweight pin_group proxy. Cold consumers materialize
real pin_layout objects on demand; anything that needs to mutate pins in
place converts the group back to the plain Python dict (pythonize).
"""
from openram import debug
from openram import tech
from openram.tech import GDS, layer
from .vector import vector
from .pin_layout import pin_layout

_MIRROR_CODES = {"MX": 1, "MY": 2, "XY": 3}

# layer name -> (layer_num, purpose, second_lpp_or_None, label_purpose, zoom)
# Exact port of the resolution in pin_layout.gds_write_file.
_layer_specs = {}


def _same_lpp(lpp1, lpp2):
    if lpp1[1] is None or lpp2[1] is None:
        return lpp1[0] == lpp2[0]
    return lpp1[0] == lpp2[0] and lpp1[1] == lpp2[1]


def _layer_spec(layer_name):
    try:
        return _layer_specs[layer_name]
    except KeyError:
        pass
    try:
        (pin_layer_num, pin_purpose) = layer[layer_name + "p"]
    except KeyError:
        (pin_layer_num, pin_purpose) = layer[layer_name]
    (layer_num, purpose) = layer[layer_name]

    (global_pin_purpose, has_label_purpose,
     label_purpose, layer_override_purpose) = pin_layout._get_tech_purposes()

    if global_pin_purpose is not None:
        pin_purpose = global_pin_purpose

    if has_label_purpose:
        if pin_layer_num in layer_override_purpose:
            layer_num = layer_override_purpose[pin_layer_num][0]
            label_purpose = layer_override_purpose[pin_layer_num][1]
    else:
        label_purpose = purpose

    second = None
    if not _same_lpp((pin_layer_num, pin_purpose), (layer_num, purpose)):
        second = (pin_layer_num, pin_purpose)
    try:
        zoom = GDS["zoom"]
    except KeyError:
        zoom = None
    spec = (layer_num, purpose, second, label_purpose, zoom)
    _layer_specs[layer_name] = spec
    return spec


def _make_pin(name, layer_name, llx, lly, urx, ury):
    """ pin_layout handle from a stored (already snapped) rect. Bypasses
    __init__ so the rect is not re-snapped (snap is not float-idempotent). """
    p = pin_layout.__new__(pin_layout)
    p.name = name
    p._rect = [vector(llx, lly), vector(urx, ury)]
    p._layer = layer_name
    p.lpp = layer[layer_name]
    p._hash = None
    return p


class _store_state:
    """ Per-design PinStore handle plus the Python-side layer id mirror. """
    __slots__ = ("rs_store", "layer_ids", "layer_names", "masters")

    def __init__(self, rs_store):
        self.rs_store = rs_store
        self.layer_ids = {}
        self.layer_names = []
        self.masters = {}

    def layer_id(self, name):
        try:
            return self.layer_ids[name]
        except KeyError:
            return self._add_layer(name, str(layer[name]))

    def layer_id_for(self, name, lpp_str):
        try:
            return self.layer_ids[name]
        except KeyError:
            return self._add_layer(name, lpp_str)

    def _add_layer(self, name, lpp_str):
        lid = self.rs_store.add_layer(name, lpp_str)
        self.layer_ids[name] = lid
        self.layer_names.append(name)
        return lid


def _get_state(design, rs):
    state = getattr(design, "_rust_pin_state", None)
    if state is None:
        state = _store_state(rs.PinStore(float(tech.drc["grid"])))
        design._rust_pin_state = state
    return state


class pin_group:
    """
    pin_map value backed by a Rust PinStore group. Mimics the
    insertion-ordered dict-of-pins: len(), iteration (materialized
    pin_layout handles, memoized per store revision), and the hot
    consumers (bounds, GDS emission) served straight from the store.
    """
    __slots__ = ("state", "name", "gid", "_mat", "_mat_rev")

    def __init__(self, state, name, gid):
        self.state = state
        self.name = name
        self.gid = gid
        self._mat = None
        self._mat_rev = -1

    def __len__(self):
        return self.state.rs_store.group_len(self.gid)

    def __iter__(self):
        return iter(self._pins())

    def _pins(self):
        rev = self.state.rs_store.group_rev(self.gid)
        if self._mat is None or self._mat_rev != rev:
            names = self.state.layer_names
            name = self.name
            self._mat = [_make_pin(name, names[lid], a, b, c, d)
                         for (lid, a, b, c, d)
                         in self.state.rs_store.materialize(self.gid)]
            self._mat_rev = rev
        return self._mat

    def pythonize(self):
        """ Convert back to the plain insertion-ordered dict for consumers
        that mutate pins in place (e.g. translate_all). """
        return {p: p for p in self._pins()}

    def bounds(self):
        """ (min lx, min by, max rx, max uy), Python min/max tie order. """
        return self.state.rs_store.group_bounds(self.gid)

    def add(self, layer_name, offset, width, height):
        """ add_layout_pin routed into the store. Returns the pin handle
        (constructed regardless of dedup, like the Python path). """
        lid = self.state.layer_id(layer_name)
        rect = self.state.rs_store.add_pin(self.gid, lid,
                                           float(offset[0]), float(offset[1]),
                                           float(width), float(height))
        if rect is None:
            # Zero width/height after snapping: run the Python constructor
            # for its exact debug error behavior.
            return pin_layout(self.name,
                              [offset, offset + vector(width, height)],
                              layer_name)
        return _make_pin(self.name, layer_name, *rect)

    def gds_write_file(self, newLayout):
        """ Emit the group's shapes and labels; same records in the same
        order as per-pin pin_layout.gds_write_file. """
        from openram import OPTS
        data = self.state.rs_store.emit(self.gid, bool(OPTS.deterministic))
        names = self.state.layer_names
        name = self.name
        for (lid, llx, lly, w, h, cx, cy) in data:
            (layer_num, purpose, second, label_purpose, zoom) = \
                _layer_spec(names[lid])
            newLayout.addBox(layerNumber=layer_num,
                             purposeNumber=purpose,
                             offsetInMicrons=(llx, lly),
                             width=w,
                             height=h,
                             center=False)
            if second is not None:
                newLayout.addBox(layerNumber=second[0],
                                 purposeNumber=second[1],
                                 offsetInMicrons=(llx, lly),
                                 width=w,
                                 height=h,
                                 center=False)
            newLayout.addText(text=name,
                              layerNumber=layer_num,
                              purposeNumber=label_purpose,
                              magnification=zoom,
                              offsetInMicrons=(cx, cy))


def bulk_copy_supply_pins(design, insts, power_name, ground_name):
    """
    Whole-array fast path for bitcell_base_array.route_supplies: one
    Rust call per supply name instead of one copy_layout_pin per
    instance. Returns False when the caller must run the original loop
    (Rust unavailable, mixed Python groups, or unusual masters).
    """
    from openram.router.rust_router import load_openram_rs
    rs = load_openram_rs()
    if rs is None:
        return False

    for new_name in ("vdd", "gnd"):
        group = design.pin_map.get(new_name)
        if group is not None and not isinstance(group, pin_group):
            return False

    state = _get_state(design, rs)
    # (master, ox, oy, mirror, rotate) per instance, in instance order;
    # names in pin_map key creation order (first encounter).
    batches = {"vdd": [], "gnd": []}
    name_order = []
    for inst in insts:
        mod = inst.mod
        for (pin_name, new_name) in ((power_name, "vdd"),
                                     (ground_name, "gnd")):
            if pin_name not in mod.pins:
                continue
            src = None
            pmap = getattr(mod, "pin_map", None)
            if isinstance(pmap, dict):
                src = pmap.get(mod.get_pin_name(pin_name))
            if isinstance(src, pin_group):
                # Nested store groups: the per-instance path handles them.
                return False
            key = (id(mod), pin_name)
            master = state.masters.get(key)
            if master is None:
                data = []
                for p in mod.get_pins(pin_name):
                    l = p.layer
                    if not isinstance(l, str):
                        return False
                    (ll, ur) = p.rect
                    data.append((state.layer_id(l),
                                 float(ll.x), float(ll.y),
                                 float(ur.x), float(ur.y)))
                master = state.rs_store.intern_master(data) if data else "empty"
                state.masters[key] = master
            if master == "empty":
                # Spice pin without layout pins: same warning, no group.
                debug.warning("Could not find pin {0} on {1}".format(pin_name,
                                                                     mod.name))
                continue
            if new_name not in name_order:
                name_order.append(new_name)
            batches[new_name].append((master,
                                      float(inst.offset.x),
                                      float(inst.offset.y),
                                      _MIRROR_CODES.get(inst.mirror, 0),
                                      int(inst.rotate) if inst.rotate else 0))

    for new_name in name_order:
        group = design.pin_map.get(new_name)
        if group is None:
            group = pin_group(state, new_name, state.rs_store.new_group())
            design.pin_map[new_name] = group
        if not state.rs_store.copy_pins_batch(group.gid, batches[new_name],
                                              0.0, 0.0):
            design.pin_map[new_name] = group.pythonize()
            return False
    return True


def copy_layout_pin_rust(design, instance, pin_name, new_name, relative_offset):
    """
    Fast path for hierarchy_layout.copy_layout_pin. Returns True when the
    copy was fully handled store-side; False means the caller must run the
    original Python path (mixed Python group, non-string layers, or a rect
    that collapses after snapping).
    """
    from openram.router.rust_router import load_openram_rs
    rs = load_openram_rs()
    if rs is None:
        return False

    if new_name == "":
        new_name = pin_name

    group = design.pin_map.get(new_name)
    if group is not None and not isinstance(group, pin_group):
        # Existing plain-Python group: dedup would have to span both
        # representations; leave the whole name to the Python path.
        return False

    mod = instance.mod
    mirror = _MIRROR_CODES.get(instance.mirror, 0)
    rotate = int(instance.rotate) if instance.rotate else 0
    ox = float(instance.offset.x)
    oy = float(instance.offset.y)
    relx = float(relative_offset[0])
    rely = float(relative_offset[1])
    state = _get_state(design, rs)

    src = None
    pmap = getattr(mod, "pin_map", None)
    if isinstance(pmap, dict):
        src = pmap.get(mod.get_pin_name(pin_name))

    if isinstance(src, pin_group):
        # Store-to-store bulk copy (e.g. bitcell array vdd/gnd -> bank).
        src_state = src.state
        idmap = [state.layer_id_for(nm, lpp)
                 for (nm, lpp) in src_state.rs_store.layer_table()]
        if group is None:
            group = pin_group(state, new_name, state.rs_store.new_group())
            design.pin_map[new_name] = group
        if not state.rs_store.copy_from(src_state.rs_store, src.gid, idmap,
                                        ox, oy, relx, rely, mirror, rotate,
                                        group.gid):
            # A pin collapsed after snapping. Hand the whole group back to
            # the Python path: already-copied pins dedup there, and the
            # offending pin reproduces the exact construction error.
            design.pin_map[new_name] = group.pythonize()
            return False
        return True

    pins = list(mod.get_pins(pin_name))
    if len(pins) == 0:
        debug.warning("Could not find pin {0} on {1}".format(pin_name,
                                                             mod.name))
        return True

    key = (id(mod), pin_name)
    master = state.masters.get(key)
    if master is None:
        data = []
        for p in pins:
            l = p.layer
            if not isinstance(l, str):
                return False
            (ll, ur) = p.rect
            data.append((state.layer_id(l),
                         float(ll.x), float(ll.y), float(ur.x), float(ur.y)))
        master = state.rs_store.intern_master(data)
        state.masters[key] = master

    if group is None:
        group = pin_group(state, new_name, state.rs_store.new_group())
        design.pin_map[new_name] = group
    if not state.rs_store.copy_pins(group.gid, master, ox, oy,
                                    relx, rely, mirror, rotate):
        design.pin_map[new_name] = group.pythonize()
        return False
    return True

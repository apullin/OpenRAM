# See LICENSE for licensing information.
#
# Copyright (c) 2016-2026 Regents of the University of California, Santa Cruz
# All rights reserved.
#
"""
Export the spice netlist into the Rust NetlistDb and serialize it there.
The traversal below captures exactly what sp_write_file reads: module
identity (name) for deduplication, cell names, pin order and types,
comments, device templates, instance connections, and trim sets.
"""
from openram import OPTS


def export_netlist(design):
    from openram.router.rust_router import load_openram_rs
    from openram.base.hierarchy_spice import netlist_rev
    rev = netlist_rev[0]
    cache = getattr(design, "_rust_netlist_cache", None)
    if cache is not None and cache[0] == rev:
        return cache[1]
    rs = load_openram_rs()
    db = rs.NetlistDb()
    memo = {}

    def export(mod):
        key = id(mod)
        try:
            return memo[key]
        except KeyError:
            pass
        spice_text = "\n".join(mod.spice) if mod.spice else None
        lvs_text = "\n".join(mod.lvs) if hasattr(mod, "lvs") else None
        # contact/wire modules keep pins as a plain list; they are
        # no_instances (or pinless) so sp_write_file never reads types.
        if hasattr(mod.pins, "values"):
            pins = [(str(p.name), str(p.type)) for p in mod.pins.values()]
        else:
            pins = []
        comments = [str(c) for c in getattr(mod, "comments", [])]
        mid = db.add_module(str(mod.name),
                            str(mod.cell_name),
                            bool(mod.no_instances),
                            spice_text,
                            lvs_text,
                            pins,
                            comments,
                            getattr(mod, "spice_device", None),
                            getattr(mod, "lvs_device", None),
                            [str(x) for x in mod.trim_insts])
        memo[key] = mid
        if not mod.spice:
            # Children in the exact order sp_write_file iterates them
            if OPTS.deterministic:
                children = sorted(mod.mods, key=lambda m: m.cell_name)
            else:
                children = list(mod.mods)
            db.set_children(mid, [export(m) for m in children])
            for inst in mod.insts:
                has_pins = len(inst.spice_pins) > 0
                conns = inst.get_connections() if has_pins else []
                db.add_inst(mid, str(inst.name), export(inst.mod),
                            [str(c) for c in conns], has_pins)
        return mid

    db.set_top(export(design))
    design._rust_netlist_cache = (rev, db)
    return db

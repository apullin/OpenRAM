# See LICENSE for licensing information.
#
# Copyright (c) 2016-2026 Regents of the University of California, Santa Cruz
# All rights reserved.
#
"""
Rust-backed GDS reader exposing the subset of gdsMill's VlsiLayout that the
graph routers use (getAllShapes / getAllPinShapes). Semantics — including
processLabelPins' label-to-shape association and its reuse of the `shapes`
list across labels when a layer_override hits — mirror vlsiLayout.py.
"""
from .rust_router import load_openram_rs


class _export_collector:
    """
    Stand-in for VlsiLayout that records the top structure's elements as
    export tuples. It receives the exact addBox/addText/addInstance calls
    the per-object gds_write_file methods make, so element semantics and
    ordering are inherited rather than replicated. Children are untouched
    (their own visited lists keep them cached).
    """

    def __init__(self, units):
        self.units = units
        self.boundaries = []
        self.texts = []
        self.srefs = []

    def userUnits(self, microns):
        # Same computation as vlsiLayout.userUnits
        layoutUnitsPerMicron = 1.0 / self.units[0]
        return round(microns * layoutUnitsPerMicron, 0)

    def addBox(self, layerNumber=0, purposeNumber=0, offsetInMicrons=(0, 0),
               width=1.0, height=1.0, center=False):
        o = (self.userUnits(offsetInMicrons[0]),
             self.userUnits(offsetInMicrons[1]))
        w = self.userUnits(width)
        h = self.userUnits(height)
        if not center:
            start = o
        else:
            start = (o[0] - w / 2.0, o[1] - h / 2.0)
        flat = [start[0], start[1],
                start[0] + w, start[1],
                start[0] + w, start[1] + h,
                start[0], start[1] + h,
                start[0], start[1]]
        purpose = purposeNumber if isinstance(purposeNumber, int) else 0
        self.boundaries.append((layerNumber, purpose, flat))

    def addText(self, text, layerNumber=0, purposeNumber=0,
                offsetInMicrons=(0, 0), magnification=None, rotate=None):
        o = (self.userUnits(offsetInMicrons[0]),
             self.userUnits(offsetInMicrons[1]))
        purpose = purposeNumber if isinstance(purposeNumber, int) else 0
        # GdsText defaults transFlags to [0,0,0] (STRANS always written);
        # magFactor/rotateAngle only when set (addText semantics).
        mag = float(magnification) if magnification else None
        angle = float(rotate) if rotate else None
        self.texts.append((str(text), layerNumber, purpose,
                           float(o[0]), float(o[1]),
                           False, mag, angle))

    def addInstance(self, layoutToAdd, nameOfLayout=0, offsetInMicrons=(0, 0),
                    mirror=None, rotate=None):
        o = (self.userUnits(offsetInMicrons[0]),
             self.userUnits(offsetInMicrons[1]))
        if nameOfLayout == 0:
            name = str(layoutToAdd.rootStructureName)
        else:
            name = str(nameOfLayout)
        if name.endswith("\x00"):
            name = name.rstrip("\x00")
        # Same mirror/rotate resolution as vlsiLayout.addInstance.
        # GdsSref defaults transFlags to [0,0,0], so STRANS is always
        # present; rotateAngle is set (ANGLE record) only when assigned.
        mirror_x = False
        angle = None
        if mirror or rotate:
            if mirror == "R90":
                rotate = 90.0
            if mirror == "R180":
                rotate = 180.0
            if mirror == "R270":
                rotate = 270.0
            if rotate:
                angle = float(rotate)
            if mirror == "x" or mirror == "MX":
                mirror_x = True
            if mirror == "y" or mirror == "MY":
                mirror_x = True
                angle = 180.0
            if mirror == "xy" or mirror == "XY":
                angle = 180.0
        self.srefs.append((name, float(o[0]), float(o[1]),
                           mirror_x, None, angle))

    def addPath(self, *args, **kwargs):
        raise NotImplementedError(
            "path objects are not supported by the Rust GDS export")


def export_design(design):
    """
    Export the design into a cached Rust GdsLayout, skipping the GDS
    serialize/parse round-trip. The first call builds the gdsMill layout
    once (populating all child structures); later calls re-export only
    the top structure through _export_collector since children are
    immutable once built.
    """
    from openram import debug
    from openram.gdsMill import gdsMill
    from openram.tech import GDS

    rs = load_openram_rs()

    rl = getattr(design, "_rust_gds_layout", None)
    if rl is not None:
        top = str(design.gds.rootStructureName)
        if top.endswith("\x00"):
            top = top.rstrip("\x00")
        collector = _export_collector(GDS["unit"])
        design.clear_visited()
        design.gds_write_file(collector)
        # New child modules (e.g. via masters created by a later router)
        # need their structure trees exported too.
        for inst in design.insts:
            cell_name = str(inst.mod.cell_name)
            if not rl.has_structure(cell_name):
                _export_structures(inst.mod.gds, rl, None)
        rl.add_structure(top, collector.boundaries, collector.srefs,
                         collector.texts)
        rl.set_root(top)
        return rl

    # First export: build every structure straight into the Rust layout.
    # Generated modules are captured through _export_collector (the same
    # addBox/addText/addInstance calls gds_write_file makes); library
    # cells keep their file-read gdsMill layouts (plus the pin_map pins
    # gds_write_file appends to them once) and are converted wholesale.
    # Structure order replicates the VlsiLayout.addInstance merge: each
    # module's own structure precedes its children's subtrees, children
    # in instance order, first encounter wins.
    design.clear_visited()

    units = GDS["unit"]
    rl = rs.GdsLayout.empty(units[0], units[1])
    design._rust_gds_layout = rl

    order = []
    _order_walk(design, order, set())
    staging = {}
    _build_walk(design, staging, units)
    for sname in order:
        (boundaries, srefs, texts) = staging[sname]
        rl.add_structure(sname, boundaries, srefs, texts)

    root = str(design.gds.rootStructureName)
    if root.endswith("\x00"):
        root = root.rstrip("\x00")
    rl.set_root(root)
    return rl


def _strip_name(name):
    name = str(name)
    if name.endswith("\x00"):
        name = name.rstrip("\x00")
    return name


def _order_walk(mod, order, seen):
    """ Structure-name order as the gdsMill merge would produce it. """
    if mod.is_library_cell:
        # addInstance merges the whole file-read structure dict.
        for key in mod.gds.structures:
            n = _strip_name(key)
            if n not in seen:
                seen.add(n)
                order.append(n)
        return
    n = str(mod.name)
    if n in seen:
        return
    seen.add(n)
    order.append(n)
    for inst in mod.insts:
        _order_walk(inst.mod, order, seen)


def _build_walk(mod, staging, units):
    """ Post-order structure content build. Children are built (and marked
    visited) first, so each module's own gds_write_file(collector) call
    sees visited children and only emits its own elements. """
    if mod.is_library_cell:
        n = _strip_name(mod.gds.rootStructureName)
        if n in staging:
            return
        # Appends the module's pin_map pins into its layout once
        # (visited-guarded), exactly as the full build did.
        mod.gds_write_file(mod.gds)
        for key, s in mod.gds.structures.items():
            sn = _strip_name(key)
            if sn not in staging:
                staging[sn] = _convert_structure(s)
        return
    n = str(mod.name)
    if n in staging:
        return
    for inst in mod.insts:
        _build_walk(inst.mod, staging, units)
    collector = _export_collector(units)
    mod.gds_write_file(collector)
    staging[n] = (collector.boundaries, collector.srefs, collector.texts)


def _convert_structure(s):
    """ One gdsMill structure -> (boundaries, srefs, texts) export tuples. """
    boundaries = []
    for b in s.boundaries:
        purpose = b.purposeLayer if isinstance(b.purposeLayer, int) else 0
        flat = []
        for c in b.coordinates:
            flat.append(float(c[0]))
            flat.append(float(c[1]))
        boundaries.append((b.drawingLayer, purpose, flat))
    srefs = []
    for sref in s.srefs:
        child = _strip_name(sref.sName)
        strans = (None if sref.transFlags == ""
                  else bool(sref.transFlags[0]))
        mag = None if sref.magFactor in ("", None) else float(sref.magFactor)
        angle = (None if sref.rotateAngle in ("", None)
                 else float(sref.rotateAngle))
        srefs.append((child,
                      float(sref.coordinates[0]),
                      float(sref.coordinates[1]),
                      strans, mag, angle))
    texts = []
    for t in s.texts:
        string = _strip_name(t.textString)
        purpose = t.purposeLayer if isinstance(t.purposeLayer, int) else 0
        strans = (None if t.transFlags == ""
                  else bool(t.transFlags[0]))
        mag = None if t.magFactor in ("", None) else float(t.magFactor)
        angle = (None if t.rotateAngle in ("", None)
                 else float(t.rotateAngle))
        texts.append((string, t.drawingLayer, purpose,
                      float(t.coordinates[0][0]),
                      float(t.coordinates[0][1]),
                      strans, mag, angle))
    return (boundaries, srefs, texts)


def _export_structures(layout, rl, replace_root):
    """ Convert a gdsMill layout's structures into the Rust layout,
    skipping structures already exported (the root is always resent
    when replace_root names it). """
    for name, s in layout.structures.items():
        sname = _strip_name(name)
        if sname != replace_root and rl.has_structure(sname):
            continue
        (boundaries, srefs, texts) = _convert_structure(s)
        rl.add_structure(sname, boundaries, srefs, texts)


def _same_lpp(lpp1, lpp2):
    if lpp1[1] is None or lpp2[1] is None:
        return lpp1[0] == lpp2[0]
    return lpp1[0] == lpp2[0] and lpp1[1] == lpp2[1]


class rust_layout:
    """ Drop-in for VlsiLayout in the router's read path. """

    def __init__(self, gds_filename=None, units=(0.001, 1e-9), layout=None):
        if layout is not None:
            self._layout = layout
        else:
            rs = load_openram_rs()
            self._layout = rs.GdsLayout(gds_filename)
        self.units = units
        self.pins = {}
        self._process_all_label_pins()

    def getAllShapes(self, lpp):
        purpose = lpp[1] if lpp[1] is not None else -1
        return self._layout.get_all_shapes(lpp[0], purpose)

    def getAllPinShapes(self, pin_name):
        shape_list = []
        for pin_list in self.pins[pin_name]:
            for pin in pin_list:
                shape_list.append(pin)
        return shape_list

    def _process_all_label_pins(self):
        """ initialize(): find enclosing shapes for every root label. """

        texts = self._layout.root_texts()
        unit = self.units[0]
        # Resolve layer_override once: a failed `from openram.tech import`
        # is not cached by the import system, so retrying it per label
        # costs a full module search each time.
        try:
            from openram.tech import layer_override
        except ImportError:
            layer_override = {}
        for layer_number in self._layout.layers_in_use():
            lpp = (layer_number, None)
            labels = [t for t in texts if _same_lpp((t[1], t[2]), lpp)]
            if not labels:
                continue
            shapes = self.getAllShapes(lpp)
            for (string, _layer, _purpose, x, y) in labels:
                user_coordinate = [x * unit, y * unit]
                pin_shapes = []
                label_text = string
                override = layer_override.get(label_text)
                if override:
                    shapes = self.getAllShapes((override[0], None))
                    if not shapes:
                        shapes = self.getAllShapes(lpp)
                    else:
                        lpp = override
                for boundary in shapes:
                    if (boundary[0] <= user_coordinate[0] <= boundary[2] and
                            boundary[1] <= user_coordinate[1] <= boundary[3]):
                        pin_shapes.append((lpp, boundary))
                if label_text not in self.pins:
                    self.pins[label_text] = []
                self.pins[label_text].append(pin_shapes)

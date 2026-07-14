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


def _same_lpp(lpp1, lpp2):
    if lpp1[1] is None or lpp2[1] is None:
        return lpp1[0] == lpp2[0]
    return lpp1[0] == lpp2[0] and lpp1[1] == lpp2[1]


class rust_layout:
    """ Drop-in for VlsiLayout in the router's read path. """

    def __init__(self, gds_filename, units=(0.001, 1e-9)):
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
                try:
                    from openram.tech import layer_override
                    if layer_override[label_text]:
                        shapes = self.getAllShapes(
                            (layer_override[label_text][0], None))
                        if not shapes:
                            shapes = self.getAllShapes(lpp)
                        else:
                            lpp = layer_override[label_text]
                except Exception:
                    pass
                for boundary in shapes:
                    if (boundary[0] <= user_coordinate[0] <= boundary[2] and
                            boundary[1] <= user_coordinate[1] <= boundary[3]):
                        pin_shapes.append((lpp, boundary))
                if label_text not in self.pins:
                    self.pins[label_text] = []
                self.pins[label_text].append(pin_shapes)

use crate::snap::py_round;

/// A routing shape crossing the Python boundary. `rect` is the (possibly
/// inflated) shape used for region/tree queries; `core` is the original
/// un-inflated shape (graph_shape.get_core()). Both are snapped to the
/// grid by graph_shape's constructor on the Python side.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ShapeIn {
    pub llx: f64,
    pub lly: f64,
    pub urx: f64,
    pub ury: f64,
    pub cllx: f64,
    pub clly: f64,
    pub curx: f64,
    pub cury: f64,
    /// Interned pin/net name (graph.is_routable compares names).
    pub name: u32,
    /// Interned exact (layer, purpose) pair (pin_layout.__eq__ semantics).
    pub lpp: u32,
    /// router.get_zindex(shape.lpp)
    pub zindex: i8,
    /// same_lpp(shape.lpp, route_lpp[z]) for z in 0, 1
    pub same_route_lpp: [bool; 2],
    /// GDS layer number (lpp[0]); used by the router store's layer filters.
    pub layer_num: i16,
    /// GDS purpose (lpp[1]); -1 encodes Python None (same_lpp wildcard).
    pub purpose: i32,
}

impl ShapeIn {
    /// pin_layout.same_lpp: purpose None matches anything.
    pub fn same_lpp_wild(&self, other: &ShapeIn) -> bool {
        if self.purpose < 0 || other.purpose < 0 {
            return self.layer_num == other.layer_num;
        }
        self.layer_num == other.layer_num && self.purpose == other.purpose
    }
}

impl ShapeIn {
    /// graph_shape.width(): snapped |urx - llx| of the core.
    pub fn core_width(&self, nd: usize) -> f64 {
        py_round((self.curx - self.cllx).abs(), nd)
    }

    /// graph_shape.height(): snapped |ury - lly| of the core.
    pub fn core_height(&self, nd: usize) -> f64 {
        py_round((self.cury - self.clly).abs(), nd)
    }

    /// graph_shape.center(): snapped midpoint of the core.
    pub fn core_center(&self, nd: usize) -> (f64, f64) {
        (
            py_round(0.5 * (self.cllx + self.curx), nd),
            py_round(0.5 * (self.clly + self.cury), nd),
        )
    }

    /// pin_layout equality used for `shape == blockage.get_core()`:
    /// exact lpp tuple equality plus rect equality (on core rects).
    pub fn core_eq(&self, other: &ShapeIn) -> bool {
        self.lpp == other.lpp
            && self.cllx == other.cllx
            && self.clly == other.clly
            && self.curx == other.curx
            && self.cury == other.cury
    }
}

/// pin_layout.xoverlaps/yoverlaps on two rects (closed intervals).
pub fn rects_overlap(
    allx: f64,
    ally: f64,
    aurx: f64,
    aury: f64,
    bllx: f64,
    blly: f64,
    burx: f64,
    bury: f64,
) -> bool {
    let x = (bllx <= allx && allx <= burx)
        || (bllx <= aurx && aurx <= burx)
        || (allx <= bllx && bllx <= aurx)
        || (allx <= burx && burx <= aurx);
    if !x {
        return false;
    }
    (blly <= ally && ally <= bury)
        || (blly <= aury && aury <= bury)
        || (ally <= blly && blly <= aury)
        || (ally <= bury && bury <= aury)
}

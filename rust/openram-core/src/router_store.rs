// Rust-owned blockage/via/pin state for one router run: the port of the
// list-management half of compiler/router/router.py (find_blockages'
// merge/survivor logic, find_vias dedup, convert_vias/convert_blockages
// naming). Shapes arrive already inflated (inflate_shape stays in Python
// because it reads the width-parameterized DRC tables); the routing graph
// reads the store's vectors directly, so nothing is reconverted per pair.

use crate::graph::{route_over, Tech};
use crate::shape::ShapeIn;
use std::collections::HashMap;

pub struct RouterStore {
    pub tech: Tech,
    pub blockages: Vec<ShapeIn>,
    pub vias: Vec<ShapeIn>,
    pub pins: Vec<ShapeIn>,
    names: HashMap<String, u32>,
    lpps: HashMap<String, u32>,
    via_name: u32,
}

/// pin_layout closed-interval rect overlap on core rects.
fn core_overlap(a: &ShapeIn, b: &ShapeIn) -> bool {
    crate::shape::rects_overlap(
        a.cllx, a.clly, a.curx, a.cury, b.cllx, b.clly, b.curx, b.cury,
    )
}

/// graph_shape.core_contained_by_any fast path: same_lpp wildcard plus
/// core containment (closed intervals).
fn core_contained_by(shape: &ShapeIn, others: &[&ShapeIn]) -> bool {
    for other in others {
        if !other.same_lpp_wild(shape) {
            continue;
        }
        if shape.cllx >= other.cllx
            && shape.curx <= other.curx
            && shape.clly >= other.clly
            && shape.cury <= other.cury
        {
            return true;
        }
    }
    false
}

impl RouterStore {
    pub fn new(tech: Tech) -> RouterStore {
        let mut store = RouterStore {
            tech,
            blockages: Vec::new(),
            vias: Vec::new(),
            pins: Vec::new(),
            names: HashMap::new(),
            lpps: HashMap::new(),
            via_name: 0,
        };
        store.via_name = store.intern_name("via");
        store
    }

    pub fn intern_name(&mut self, name: &str) -> u32 {
        let next = self.names.len() as u32;
        *self.names.entry(name.to_string()).or_insert(next)
    }

    pub fn intern_lpp(&mut self, lpp: &str) -> u32 {
        let next = self.lpps.len() as u32;
        *self.lpps.entry(lpp.to_string()).or_insert(next)
    }

    pub fn add_pin(&mut self, pin: ShapeIn) {
        self.pins.push(pin);
    }

    pub fn append_blockage(&mut self, shape: ShapeIn) {
        self.blockages.push(shape);
    }

    /// router.merge_shapes fast path: merge contained/aligned entries of
    /// `layer` (indices into a scratch arena) into `merger`, dropping them.
    fn merge_shapes(merger: &mut ShapeIn, layer: &mut Vec<usize>, arena: &[ShapeIn], removed: &mut [bool]) {
        let mut kept: Vec<usize> = Vec::with_capacity(layer.len());
        for &i in layer.iter() {
            let shape = &arena[i];
            let same_lpp = merger.lpp == shape.lpp
                || (merger.layer_num == shape.layer_num
                    && (merger.purpose < 0
                        || shape.purpose < 0
                        || merger.purpose == shape.purpose));
            let contained = same_lpp
                && shape.cllx >= merger.cllx
                && shape.curx <= merger.curx
                && shape.clly >= merger.clly
                && shape.cury <= merger.cury;
            let mut aligned = false;
            if same_lpp && !contained {
                let x_overlaps = (shape.cllx <= merger.cllx && merger.cllx <= shape.curx)
                    || (shape.cllx <= merger.curx && merger.curx <= shape.curx)
                    || (merger.cllx <= shape.cllx && shape.cllx <= merger.curx)
                    || (merger.cllx <= shape.curx && shape.curx <= merger.curx);
                if x_overlaps {
                    let y_overlaps = (shape.clly <= merger.clly && merger.clly <= shape.cury)
                        || (shape.clly <= merger.cury && merger.cury <= shape.cury)
                        || (merger.clly <= shape.clly && shape.clly <= merger.cury)
                        || (merger.clly <= shape.cury && shape.cury <= merger.cury);
                    aligned = y_overlaps
                        && ((merger.cllx == shape.cllx && merger.curx == shape.curx)
                            || (merger.clly == shape.clly && merger.cury == shape.cury));
                }
            }
            if contained {
                removed[i] = true;
            } else if aligned {
                // merger.bbox([shape]) on the inflated rect and the core
                merger.llx = merger.llx.min(shape.llx);
                merger.lly = merger.lly.min(shape.lly);
                merger.urx = merger.urx.max(shape.urx);
                merger.ury = merger.ury.max(shape.ury);
                merger.cllx = merger.cllx.min(shape.cllx);
                merger.clly = merger.clly.min(shape.clly);
                merger.curx = merger.curx.max(shape.curx);
                merger.cury = merger.cury.max(shape.cury);
                removed[i] = true;
            } else {
                kept.push(i);
            }
        }
        *layer = kept;
    }

    /// router.find_blockages for one routing lpp: `shapes` are the new,
    /// already-inflated candidates on that layer, in order.
    pub fn find_blockages_layer(&mut self, layer_num: i16, shapes: Vec<ShapeIn>) {
        if shapes.is_empty() {
            return;
        }
        let layer_pins: Vec<&ShapeIn> = self
            .pins
            .iter()
            .filter(|p| p.layer_num == layer_num)
            .collect();

        // Arena: existing store blockages on this layer, then added shapes.
        // `layer` holds arena indices in list order; `removed` marks drops.
        let n_existing = self.blockages.len();
        let mut arena: Vec<ShapeIn> = Vec::new();
        std::mem::swap(&mut arena, &mut self.blockages);
        let mut layer: Vec<usize> = (0..n_existing)
            .filter(|&i| arena[i].layer_num == layer_num)
            .collect();
        let original: Vec<usize> = layer.clone();
        let mut removed = vec![false; n_existing + shapes.len()];
        let mut added: Vec<usize> = Vec::new();

        for new_shape in shapes {
            let contained = {
                let layer_refs: Vec<&ShapeIn> = layer.iter().map(|&i| &arena[i]).collect();
                core_contained_by(&new_shape, &layer_pins)
                    || core_contained_by(&new_shape, &layer_refs)
            };
            if contained {
                continue;
            }
            let mut merger = new_shape;
            Self::merge_shapes(&mut merger, &mut layer, &arena, &mut removed);
            let idx = arena.len();
            arena.push(merger);
            if removed.len() <= idx {
                removed.push(false);
            }
            layer.push(idx);
            added.push(idx);
        }

        if added.is_empty() {
            // Python skips the rebuild when nothing was added; no shape can
            // have been removed in that case either.
            self.blockages = arena;
            self.blockages.truncate(n_existing);
            return;
        }

        // Survivor rebuild: keep non-layer and surviving-layer shapes in
        // order, then append surviving added shapes in insertion order.
        let was_original = |i: usize| original.binary_search(&i).is_ok();
        let mut rebuilt: Vec<ShapeIn> = Vec::with_capacity(arena.len());
        for i in 0..n_existing {
            if !was_original(i) || !removed[i] {
                rebuilt.push(arena[i].clone());
            }
        }
        for &i in &added {
            if !removed[i] {
                rebuilt.push(arena[i].clone());
            }
        }
        self.blockages = rebuilt;
    }

    /// router.find_vias: `shapes` are (raw, inflated) pairs in order.
    pub fn find_vias(&mut self, shapes: Vec<(ShapeIn, ShapeIn)>) {
        for (raw, inflated) in shapes {
            // contained_by_any against existing (inflated) vias, using the
            // raw shape's rect (closed intervals) and real same_lpp.
            let mut contained = false;
            for via in &self.vias {
                if !via.same_lpp_wild(&raw) {
                    continue;
                }
                if raw.llx >= via.llx
                    && raw.urx <= via.urx
                    && raw.lly >= via.lly
                    && raw.ury <= via.ury
                {
                    contained = true;
                    break;
                }
            }
            if !contained {
                self.vias.push(inflated);
            }
        }
    }

    /// router.convert_vias: rename a via after the first pin whose core
    /// its core overlaps (the lpp is forced equal in Python, so this is a
    /// pure rectangle test).
    pub fn convert_vias(&mut self) {
        for via in self.vias.iter_mut() {
            for pin in &self.pins {
                if core_overlap(via, pin) {
                    via.name = pin.name;
                    break;
                }
            }
        }
    }

    /// router.convert_blockages: rename blockages overlapping a pin core
    /// (real same_lpp), else one overlapping an already-renamed via.
    pub fn convert_blockages(&mut self) {
        for blockage in self.blockages.iter_mut() {
            let mut renamed = false;
            for pin in &self.pins {
                if blockage.same_lpp_wild(pin) && core_overlap(blockage, pin) {
                    blockage.name = pin.name;
                    renamed = true;
                    break;
                }
            }
            if !renamed {
                for via in &self.vias {
                    if via.name == self.via_name {
                        continue;
                    }
                    if core_overlap(blockage, via) {
                        blockage.name = via.name;
                        break;
                    }
                }
            }
        }
    }

    pub fn route(&self, source: ShapeIn, target: ShapeIn) -> Option<Vec<(f64, f64, u8)>> {
        route_over(self.tech, &self.blockages, &self.vias, source, target)
    }
}

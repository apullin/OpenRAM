// See LICENSE for licensing information.
//
// Rust-owned layout pin groups (compiler/base/pin_layout.py pins held in
// hierarchy_layout.pin_map). The hot path — copy_layout_pin over big
// bitcell arrays — transforms a master pin per instance placement and
// deduplicates into a group without constructing any Python objects.
// Every float operation mirrors the Python code exactly (see the
// pin_layout.transform / add_layout_pin / vector.snap_offset_to_grid
// ports below) so that materialized pins and GDS output stay
// bit-identical with the Python backend.

use std::collections::{HashMap, HashSet};

use crate::snap::py_round;

pub struct LayerInfo {
    pub name: String,
    /// Exact Python str() of the tech lpp, used in pin_sort_key.
    pub lpp_str: String,
}

#[derive(Clone, Copy)]
pub struct StorePin {
    pub layer: u32,
    pub llx: f64,
    pub lly: f64,
    pub urx: f64,
    pub ury: f64,
}

/// Dedup key: pin_layout dict membership is hash(repr) + __eq__, where the
/// repr includes the layer name string and the shortest-repr of each
/// coordinate. String equality is therefore equivalent to raw-bit equality
/// per coordinate (with -0.0 distinct from 0.0, matching Python where the
/// differing reprs hash apart and the pins are kept as duplicates).
type Key = (u32, u64, u64, u64, u64);

fn key(p: &StorePin) -> Key {
    (
        p.layer,
        p.llx.to_bits(),
        p.lly.to_bits(),
        p.urx.to_bits(),
        p.ury.to_bits(),
    )
}

/// Python min()/max(): first operand wins ties (relevant for -0.0/0.0).
fn py_min(a: f64, b: f64) -> f64 {
    if b < a { b } else { a }
}

fn py_max(a: f64, b: f64) -> f64 {
    if b > a { b } else { a }
}

#[derive(Default)]
pub struct Group {
    pub pins: Vec<StorePin>,
    seen: HashSet<Key>,
    /// Bumped on every mutation so Python-side materializations can be
    /// invalidated.
    pub rev: u64,
}

pub struct PinStore {
    pub grid: f64,
    pub layers: Vec<LayerInfo>,
    layer_ids: HashMap<String, u32>,
    pub masters: Vec<Vec<StorePin>>,
    pub groups: Vec<Group>,
}

impl PinStore {
    pub fn new(grid: f64) -> Self {
        PinStore {
            grid,
            layers: Vec::new(),
            layer_ids: HashMap::new(),
            masters: Vec::new(),
            groups: Vec::new(),
        }
    }

    pub fn add_layer(&mut self, name: &str, lpp_str: &str) -> u32 {
        if let Some(&id) = self.layer_ids.get(name) {
            return id;
        }
        let id = self.layers.len() as u32;
        self.layers.push(LayerInfo {
            name: name.to_string(),
            lpp_str: lpp_str.to_string(),
        });
        self.layer_ids.insert(name.to_string(), id);
        id
    }

    pub fn new_group(&mut self) -> u32 {
        self.groups.push(Group::default());
        (self.groups.len() - 1) as u32
    }

    pub fn intern_master(&mut self, pins: Vec<StorePin>) -> u32 {
        self.masters.push(pins);
        (self.masters.len() - 1) as u32
    }

    /// vector.snap_offset_to_grid: int(round(round(offset / grid, 2), 0)) * grid
    fn snap(&self, offset: f64) -> f64 {
        let r = py_round(py_round(offset / self.grid, 2), 0);
        (r as i64) as f64 * self.grid
    }

    /// The add_layout_pin + pin_layout.__init__ tail: rect from ll + (w, h),
    /// snap both corners. Returns None when the snapped rect collapses
    /// (Python raises a debug error there; callers fall back to the Python
    /// path so the error behavior is identical).
    fn build_pin(&self, layer: u32, ox: f64, oy: f64, w: f64, h: f64) -> Option<StorePin> {
        let p = StorePin {
            layer,
            llx: self.snap(ox),
            lly: self.snap(oy),
            urx: self.snap(ox + w),
            ury: self.snap(oy + h),
        };
        if (p.urx - p.llx).abs() > 0.0 && (p.ury - p.lly).abs() > 0.0 {
            Some(p)
        } else {
            None
        }
    }

    fn push(&mut self, g: u32, p: StorePin) {
        let group = &mut self.groups[g as usize];
        if group.seen.insert(key(&p)) {
            group.pins.push(p);
            group.rev += 1;
        }
    }

    /// copy_layout_pin body for one master pin under one placement:
    /// pin_layout.transform (mirror scale, rotate_scale, offset add,
    /// normalize) followed by add_layout_pin(new_name, layer,
    /// ll + relative_offset, width, height).
    fn transform_pin(
        &self,
        m: &StorePin,
        ox: f64,
        oy: f64,
        relx: f64,
        rely: f64,
        mirror: u8,
        rotate: u16,
    ) -> Option<StorePin> {
        let (mut ax, mut ay, mut bx, mut by) = (m.llx, m.lly, m.urx, m.ury);
        match mirror {
            1 => {
                // MX: scale(1, -1)
                ay = ay * -1.0;
                by = by * -1.0;
            }
            2 => {
                // MY: scale(-1, 1)
                ax = ax * -1.0;
                bx = bx * -1.0;
            }
            3 => {
                // XY: scale(-1, -1)
                ax = ax * -1.0;
                ay = ay * -1.0;
                bx = bx * -1.0;
                by = by * -1.0;
            }
            _ => {}
        }
        match rotate {
            90 => {
                // rotate_scale(-1, 1): (y * -1, x * 1)
                let (nax, nay) = (ay * -1.0, ax * 1.0);
                let (nbx, nby) = (by * -1.0, bx * 1.0);
                ax = nax;
                ay = nay;
                bx = nbx;
                by = nby;
            }
            180 => {
                ax = ax * -1.0;
                ay = ay * -1.0;
                bx = bx * -1.0;
                by = by * -1.0;
            }
            270 => {
                // rotate_scale(1, -1): (y * 1, x * -1)
                let (nax, nay) = (ay * 1.0, ax * -1.0);
                let (nbx, nby) = (by * 1.0, bx * -1.0);
                ax = nax;
                ay = nay;
                bx = nbx;
                by = nby;
            }
            _ => {}
        }
        let (p1x, p1y) = (ox + ax, oy + ay);
        let (p2x, p2y) = (ox + bx, oy + by);
        // normalize()
        let llx = py_min(p1x, p2x);
        let lly = py_min(p1y, p2y);
        let urx = py_max(p1x, p2x);
        let ury = py_max(p1y, p2y);
        // add_layout_pin(new_name, layer, ll + relative_offset, w, h)
        let nx = llx + relx;
        let ny = lly + rely;
        let w = (urx - llx).abs();
        let h = (ury - lly).abs();
        self.build_pin(m.layer, nx, ny, w, h)
    }

    /// Returns false if any pin collapsed post-snap (caller falls back).
    pub fn copy_pins(
        &mut self,
        g: u32,
        master: u32,
        ox: f64,
        oy: f64,
        relx: f64,
        rely: f64,
        mirror: u8,
        rotate: u16,
    ) -> bool {
        let pins = std::mem::take(&mut self.masters[master as usize]);
        let mut ok = true;
        for m in &pins {
            match self.transform_pin(m, ox, oy, relx, rely, mirror, rotate) {
                Some(p) => self.push(g, p),
                None => {
                    ok = false;
                    break;
                }
            }
        }
        self.masters[master as usize] = pins;
        ok
    }

    /// Whole-array batch: one placement tuple per instance, in instance
    /// order (dedup and insertion order match the per-instance calls).
    pub fn copy_pins_batch(
        &mut self,
        g: u32,
        placements: &[(u32, f64, f64, u8, u16)],
        relx: f64,
        rely: f64,
    ) -> bool {
        let masters = std::mem::take(&mut self.masters);
        let mut ok = true;
        'outer: for &(master, ox, oy, mirror, rotate) in placements {
            for m in &masters[master as usize] {
                match self.transform_pin(m, ox, oy, relx, rely, mirror, rotate) {
                    Some(p) => self.push(g, p),
                    None => {
                        ok = false;
                        break 'outer;
                    }
                }
            }
        }
        self.masters = masters;
        ok
    }

    /// Cross-store bulk copy: source group pins (already constructed) are
    /// re-transformed under the instance placement. idmap maps source layer
    /// ids to this store's layer ids.
    pub fn copy_from(
        &mut self,
        src: &[StorePin],
        idmap: &[u32],
        ox: f64,
        oy: f64,
        relx: f64,
        rely: f64,
        mirror: u8,
        rotate: u16,
        g: u32,
    ) -> bool {
        for m in src {
            let mapped = StorePin {
                layer: idmap[m.layer as usize],
                ..*m
            };
            match self.transform_pin(&mapped, ox, oy, relx, rely, mirror, rotate) {
                Some(p) => self.push(g, p),
                None => return false,
            }
        }
        true
    }

    /// Direct add_layout_pin into a store-backed group. Returns the snapped
    /// rect for the Python-side pin handle (returned even when the pin was
    /// a duplicate, matching add_layout_pin's return of the fresh object).
    pub fn add_pin(
        &mut self,
        g: u32,
        layer: u32,
        ox: f64,
        oy: f64,
        w: f64,
        h: f64,
    ) -> Option<(f64, f64, f64, f64)> {
        let p = self.build_pin(layer, ox, oy, w, h)?;
        let rect = (p.llx, p.lly, p.urx, p.ury);
        self.push(g, p);
        Some(rect)
    }

    pub fn group_len(&self, g: u32) -> usize {
        self.groups[g as usize].pins.len()
    }

    pub fn group_rev(&self, g: u32) -> u64 {
        self.groups[g as usize].rev
    }

    /// (min lx, min by, max rx, max uy) with Python's first-wins tie
    /// handling, in insertion order.
    pub fn bounds(&self, g: u32) -> Option<(f64, f64, f64, f64)> {
        let pins = &self.groups[g as usize].pins;
        let first = pins.first()?;
        let mut b = (first.llx, first.lly, first.urx, first.ury);
        for p in &pins[1..] {
            b.0 = py_min(b.0, p.llx);
            b.1 = py_min(b.1, p.lly);
            b.2 = py_max(b.2, p.urx);
            b.3 = py_max(b.3, p.ury);
        }
        Some(b)
    }

    /// Emission order and payload for pin_layout.gds_write_file:
    /// (layer, ll, width, height, center), sorted by pin_sort_key when
    /// deterministic (name is constant within a group; str(lpp) then the
    /// rect coordinates).
    pub fn emit(&self, g: u32, deterministic: bool) -> Vec<(u32, f64, f64, f64, f64, f64, f64)> {
        let pins = &self.groups[g as usize].pins;
        let mut order: Vec<usize> = (0..pins.len()).collect();
        if deterministic {
            order.sort_by(|&a, &b| {
                let (pa, pb) = (&pins[a], &pins[b]);
                let sa = &self.layers[pa.layer as usize].lpp_str;
                let sb = &self.layers[pb.layer as usize].lpp_str;
                sa.cmp(sb)
                    .then(pa.llx.partial_cmp(&pb.llx).unwrap())
                    .then(pa.lly.partial_cmp(&pb.lly).unwrap())
                    .then(pa.urx.partial_cmp(&pb.urx).unwrap())
                    .then(pa.ury.partial_cmp(&pb.ury).unwrap())
            });
        }
        order
            .into_iter()
            .map(|i| {
                let p = &pins[i];
                (
                    p.layer,
                    p.llx,
                    p.lly,
                    (p.urx - p.llx).abs(),
                    (p.ury - p.lly).abs(),
                    0.5 * (p.llx + p.urx),
                    0.5 * (p.lly + p.ury),
                )
            })
            .collect()
    }

    pub fn raw(&self, g: u32) -> &[StorePin] {
        &self.groups[g as usize].pins
    }
}

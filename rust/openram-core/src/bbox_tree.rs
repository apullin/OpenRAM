/// Port of compiler/router/bbox_node.py: a deterministic balanced bbox tree
/// stored directly in flattened escape-index form (the Python perf branch
/// builds the node tree and then flattens; only the flat form is queried).
///
/// Flat entry: (llx, lly, urx, ury, escape, leaf_shape_index or NONE).
pub struct BboxTree {
    flat: Vec<FlatNode>,
}

#[derive(Clone, Copy)]
struct FlatNode {
    llx: f64,
    lly: f64,
    urx: f64,
    ury: f64,
    escape: u32,
    leaf: u32,
}

const NO_LEAF: u32 = u32::MAX;

struct Item {
    index: usize,
    cx2: f64,
    cy2: f64,
    llx: f64,
    lly: f64,
    urx: f64,
    ury: f64,
}

impl BboxTree {
    /// bbox_node.build(): median split on the wider center axis, with the
    /// exact same lexicographic sort key and tie-breaking as Python.
    pub fn build(rects: &[(f64, f64, f64, f64, usize)]) -> Option<BboxTree> {
        if rects.is_empty() {
            return None;
        }
        let mut items: Vec<Item> = rects
            .iter()
            .map(|&(llx, lly, urx, ury, index)| Item {
                index,
                cx2: llx + urx,
                cy2: lly + ury,
                llx,
                lly,
                urx,
                ury,
            })
            .collect();
        let mut flat: Vec<FlatNode> = Vec::with_capacity(items.len() * 2);
        Self::build_items(&mut items[..], &mut flat);
        Some(BboxTree { flat })
    }

    /// Returns the bbox of the subtree it emitted.
    fn build_items(items: &mut [Item], flat: &mut Vec<FlatNode>) -> (f64, f64, f64, f64) {
        if items.len() == 1 {
            let it = &items[0];
            flat.push(FlatNode {
                llx: it.llx,
                lly: it.lly,
                urx: it.urx,
                ury: it.ury,
                escape: flat.len() as u32 + 1,
                leaf: it.index as u32,
            });
            return (it.llx, it.lly, it.urx, it.ury);
        }
        let mut x_min = f64::INFINITY;
        let mut x_max = f64::NEG_INFINITY;
        let mut y_min = f64::INFINITY;
        let mut y_max = f64::NEG_INFINITY;
        for it in items.iter() {
            x_min = x_min.min(it.cx2);
            x_max = x_max.max(it.cx2);
            y_min = y_min.min(it.cy2);
            y_max = y_max.max(it.cy2);
        }
        let y_major = (y_max - y_min) > (x_max - x_min);
        items.sort_by(|a, b| {
            let ka = if y_major {
                [a.cy2, a.cx2, a.llx, a.lly, a.urx, a.ury, a.index as f64]
            } else {
                [a.cx2, a.cy2, a.llx, a.lly, a.urx, a.ury, a.index as f64]
            };
            let kb = if y_major {
                [b.cy2, b.cx2, b.llx, b.lly, b.urx, b.ury, b.index as f64]
            } else {
                [b.cx2, b.cy2, b.llx, b.lly, b.urx, b.ury, b.index as f64]
            };
            ka.partial_cmp(&kb).unwrap()
        });
        let middle = items.len() / 2;
        // Reserve this node's slot; fill it in after children are emitted
        // (matches Python's flatten() pre-order with escape indices).
        let slot = flat.len();
        flat.push(FlatNode {
            llx: 0.0,
            lly: 0.0,
            urx: 0.0,
            ury: 0.0,
            escape: 0,
            leaf: NO_LEAF,
        });
        let (left, right) = items.split_at_mut(middle);
        let lb = Self::build_items(left, flat);
        let rb = Self::build_items(right, flat);
        let merged = (lb.0.min(rb.0), lb.1.min(rb.1), lb.2.max(rb.2), lb.3.max(rb.3));
        flat[slot] = FlatNode {
            llx: merged.0,
            lly: merged.1,
            urx: merged.2,
            ury: merged.3,
            escape: flat.len() as u32,
            leaf: NO_LEAF,
        };
        merged
    }

    /// bbox_node.iterate_point over the flat tree. For multi-node trees the
    /// Python flat iteration starts at index 1 (the root test is skipped);
    /// a single-leaf tree tests its only entry. The visitor returns true to
    /// stop iteration (mirrors abandoning the Python generator).
    pub fn iterate_point(&self, px: f64, py: f64, mut visit: impl FnMut(usize) -> bool) {
        let end = self.flat.len();
        let mut index = if end == 1 { 0usize } else { 1usize };
        while index < end {
            let n = &self.flat[index];
            if n.llx <= px && px <= n.urx && n.lly <= py && py <= n.ury {
                index += 1;
                if n.leaf != NO_LEAF && visit(n.leaf as usize) {
                    return;
                }
            } else {
                index = n.escape as usize;
            }
        }
    }

    /// bbox_node.iterate_rect over the flat tree.
    pub fn iterate_rect(
        &self,
        sllx: f64,
        slly: f64,
        surx: f64,
        sury: f64,
        mut visit: impl FnMut(usize) -> bool,
    ) {
        let end = self.flat.len();
        let mut index = if end == 1 { 0usize } else { 1usize };
        while index < end {
            let n = &self.flat[index];
            if n.llx <= surx && sllx <= n.urx && n.lly <= sury && slly <= n.ury {
                index += 1;
                if n.leaf != NO_LEAF && visit(n.leaf as usize) {
                    return;
                }
            } else {
                index = n.escape as usize;
            }
        }
    }
}

use std::cmp::Ordering;
use std::collections::BinaryHeap;
use std::collections::HashSet;

use crate::bbox_tree::BboxTree;
use crate::shape::{rects_overlap, ShapeIn};
use crate::snap::py_round;

/// Router tech constants (already resolved on the Python side).
#[derive(Clone, Copy, Debug)]
pub struct Tech {
    pub grid: f64,
    /// decimal digits of the grid (snap precision)
    pub ndigits: usize,
    pub track_wire: f64,
    pub track_space: f64,
    pub half_wire: f64,
    /// track_width + track_space (routing region inflation)
    pub region_spacing: f64,
}

#[derive(Clone, Copy, PartialEq)]
struct Node {
    x: f64,
    y: f64,
    z: u8,
}

/// One shortest-path route request over a fixed blockage/via context.
pub struct RouteContext {
    pub tech: Tech,
    pub blockages: Vec<ShapeIn>,
    pub vias: Vec<ShapeIn>,
}

struct Graph<'a> {
    ctx: &'a RouteContext,
    source: ShapeIn,
    target: ShapeIn,
    // Indices into a merged shape list: context blockages then appended pins.
    graph_blockages: Vec<ShapeIn>,
    graph_vias: Vec<ShapeIn>,
    trees: [Option<BboxTree>; 2],
    tree_shape_of: [Vec<u32>; 2],
    via_tree: Option<BboxTree>,
    nodes: Vec<Node>,
    node_ids: Vec<u32>,
    neighbors: Vec<Vec<u32>>,
    removed: Vec<bool>,
    source_nodes: Vec<u32>,
    target_nodes: Vec<u32>,
    node_rules: (f64, f64, f64),
}

impl RouteContext {
    /// graph.create_graph + find_shortest_path for one source/target pair.
    /// Returns the path node centers, or None if unroutable.
    pub fn route(&self, source: ShapeIn, target: ShapeIn) -> Option<Vec<(f64, f64, u8)>> {
        let mut g = Graph::new(self, source, target);
        g.create();
        g.find_shortest_path()
    }
}

impl<'a> Graph<'a> {
    fn new(ctx: &'a RouteContext, source: ShapeIn, target: ShapeIn) -> Graph<'a> {
        let wide = ctx.tech.track_wire;
        let half_wide = ctx.tech.half_wire;
        let spacing = py_round(
            ctx.tech.track_space + half_wide + ctx.tech.grid,
            ctx.tech.ndigits,
        );
        Graph {
            ctx,
            source,
            target,
            graph_blockages: Vec::new(),
            graph_vias: Vec::new(),
            trees: [None, None],
            tree_shape_of: [Vec::new(), Vec::new()],
            via_tree: None,
            nodes: Vec::new(),
            node_ids: Vec::new(),
            neighbors: Vec::new(),
            removed: Vec::new(),
            source_nodes: Vec::new(),
            target_nodes: Vec::new(),
            node_rules: (wide, half_wide, spacing),
        }
    }

    fn nd(&self) -> usize {
        self.ctx.tech.ndigits
    }

    fn is_routable(&self, s: &ShapeIn) -> bool {
        s.name == self.source.name
    }

    fn create(&mut self) {
        // Routing region: bbox of source+target cores, inflated and snapped
        // (graph_shape.inflated_pin constructs a snapped graph_shape).
        let sp = self.ctx.tech.region_spacing;
        let nd = self.nd();
        let mut rllx = self.source.cllx.min(self.target.llx);
        let mut rlly = self.source.clly.min(self.target.lly);
        let mut rurx = self.source.curx.max(self.target.urx);
        let mut rury = self.source.cury.max(self.target.ury);
        rllx = py_round(rllx - sp, nd);
        rlly = py_round(rlly - sp, nd);
        rurx = py_round(rurx + sp, nd);
        rury = py_round(rury + sp, nd);

        let mut included: Vec<bool> = vec![false; self.ctx.blockages.len()];
        let mut seen: HashSet<(u32, u32, [u64; 4])> = HashSet::new();
        self.find_graph_blockages(rllx, rlly, rurx, rury, &mut included, &mut seen, true);
        self.find_graph_vias(rllx, rlly, rurx, rury);
        let (x_values, y_values) = self.generate_cartesian_values();
        // Expand the region to include "edge" shapes and rescan.
        for b in &self.graph_blockages {
            rllx = rllx.min(b.llx);
            rlly = rlly.min(b.lly);
            rurx = rurx.max(b.urx);
            rury = rury.max(b.ury);
        }
        self.find_graph_blockages(rllx, rlly, rurx, rury, &mut included, &mut seen, false);
        self.build_bbox_trees();
        self.generate_graph_nodes(&x_values, &y_values);
        self.save_end_nodes();
    }

    fn shape_key(s: &ShapeIn) -> (u32, u32, [u64; 4]) {
        (
            s.name,
            s.lpp,
            [
                s.llx.to_bits(),
                s.lly.to_bits(),
                s.urx.to_bits(),
                s.ury.to_bits(),
            ],
        )
    }

    fn find_graph_blockages(
        &mut self,
        rllx: f64,
        rlly: f64,
        rurx: f64,
        rury: f64,
        included: &mut [bool],
        seen: &mut HashSet<(u32, u32, [u64; 4])>,
        ensure_pins: bool,
    ) {
        for (i, b) in self.ctx.blockages.iter().enumerate() {
            if included[i] || seen.contains(&Self::shape_key(b)) {
                continue;
            }
            if rects_overlap(rllx, rlly, rurx, rury, b.llx, b.lly, b.urx, b.ury) {
                self.graph_blockages.push(*b);
                included[i] = true;
                seen.insert(Self::shape_key(b));
            }
        }
        if !ensure_pins {
            return;
        }
        // Make sure the source/target pins are included as blockages
        // (compared against blockage cores with pin_layout.__eq__).
        for pin in [self.source, self.target] {
            let mut found = false;
            for b in &self.graph_blockages {
                if pin.lpp == b.lpp
                    && pin.cllx == b.cllx
                    && pin.clly == b.clly
                    && pin.curx == b.curx
                    && pin.cury == b.cury
                {
                    found = true;
                    break;
                }
            }
            if !found {
                self.graph_blockages.push(pin);
                seen.insert(Self::shape_key(&pin));
            }
        }
    }

    fn find_graph_vias(&mut self, rllx: f64, rlly: f64, rurx: f64, rury: f64) {
        'outer: for v in &self.ctx.vias {
            for existing in &self.graph_vias {
                if existing.lpp == v.lpp
                    && existing.llx == v.llx
                    && existing.lly == v.lly
                    && existing.urx == v.urx
                    && existing.ury == v.ury
                {
                    continue 'outer;
                }
            }
            if rects_overlap(rllx, rlly, rurx, rury, v.llx, v.lly, v.urx, v.ury) {
                self.graph_vias.push(*v);
            }
        }
    }

    /// graph.get_safe_pin_values on a shape's core.
    fn safe_pin_values(&self, s: &ShapeIn) -> (Vec<f64>, Vec<f64>) {
        let nd = self.nd();
        let offset = self.ctx.tech.half_wire;
        let spacing = self.ctx.tech.track_space;
        let size_limit = py_round(offset * 4.0 + spacing, nd);
        let mut xs = Vec::with_capacity(2);
        let mut ys = Vec::with_capacity(2);
        if s.core_width(nd) > size_limit {
            xs.push(py_round(s.cllx + offset, nd));
            xs.push(py_round(s.curx - offset, nd));
        } else {
            xs.push(py_round(0.5 * (s.cllx + s.curx), nd));
        }
        if s.core_height(nd) > size_limit {
            ys.push(py_round(s.clly + offset, nd));
            ys.push(py_round(s.cury - offset, nd));
        } else {
            ys.push(py_round(0.5 * (s.clly + s.cury), nd));
        }
        (xs, ys)
    }

    fn generate_cartesian_values(&self) -> (Vec<f64>, Vec<f64>) {
        let nd = self.nd();
        let grid = self.ctx.tech.grid;
        let mut xs: Vec<f64> = Vec::new();
        let mut ys: Vec<f64> = Vec::new();
        for s in &self.graph_blockages {
            if !self.is_routable(s) {
                continue;
            }
            let (sx, sy) = self.safe_pin_values(s);
            xs.extend(sx);
            ys.extend(sy);
        }
        for b in &self.graph_blockages {
            xs.push(py_round(b.llx - grid, nd));
            xs.push(py_round(b.urx + grid, nd));
            ys.push(py_round(b.lly - grid, nd));
            ys.push(py_round(b.ury + grid, nd));
        }
        for v in &self.graph_vias {
            // graph_shape.center() of the via (inflated rect, snapped)
            xs.push(py_round(0.5 * (v.llx + v.urx), nd));
            ys.push(py_round(0.5 * (v.lly + v.ury), nd));
        }
        xs.sort_by(|a, b| a.total_cmp(b));
        xs.dedup();
        ys.sort_by(|a, b| a.total_cmp(b));
        ys.dedup();
        (xs, ys)
    }

    fn build_bbox_trees(&mut self) {
        for z in 0..2usize {
            let mut rects = Vec::new();
            self.tree_shape_of[z].clear();
            for (i, s) in self.graph_blockages.iter().enumerate() {
                if s.zindex == z as i8 || s.same_route_lpp[z] {
                    rects.push((s.llx, s.lly, s.urx, s.ury, self.tree_shape_of[z].len()));
                    self.tree_shape_of[z].push(i as u32);
                }
            }
            self.trees[z] = BboxTree::build(&rects);
        }
        if !self.graph_vias.is_empty() {
            let rects: Vec<_> = self
                .graph_vias
                .iter()
                .enumerate()
                .map(|(i, v)| (v.llx, v.lly, v.urx, v.ury, i))
                .collect();
            self.via_tree = BboxTree::build(&rects);
        }
    }

    /// graph.is_probe_blocked (p1, p2 on the same layer z).
    fn is_probe_blocked(&self, p1: &Node, p2: &Node) -> bool {
        let (pll_x, pur_x) = if p1.x <= p2.x { (p1.x, p2.x) } else { (p2.x, p1.x) };
        let (pll_y, pur_y) = if p1.y <= p2.y { (p1.y, p2.y) } else { (p2.y, p1.y) };
        let z = p1.z as usize;
        let tree = match &self.trees[z] {
            Some(t) => t,
            None => return false,
        };
        let mut blocked = false;
        tree.iterate_rect(pll_x, pll_y, pur_x, pur_y, |leaf| {
            let s = &self.graph_blockages[self.tree_shape_of[z][leaf] as usize];
            if !s.same_route_lpp[z] {
                return false;
            }
            if !self.is_routable(s) {
                blocked = true;
                return true;
            }
            // Routable shape: blocked if its core does not cover the probe.
            if s.cllx > pur_x || pll_x > s.curx || s.clly > pur_y || pll_y > s.cury {
                blocked = true;
                return true;
            }
            false
        });
        blocked
    }

    /// graph.is_node_blocked.
    fn is_node_blocked(&self, node: &Node) -> bool {
        let (x, y, z) = (node.x, node.y, node.z as usize);
        let tree = match &self.trees[z] {
            Some(t) => t,
            None => return false,
        };
        let nd = self.nd();
        let (wide, half_wide, spacing) = self.node_rules;
        let mut blocked = false;
        let mut early_exit = false;
        tree.iterate_point(x, y, |leaf| {
            let s = &self.graph_blockages[self.tree_shape_of[z][leaf] as usize];
            if s.zindex != z as i8 {
                return false;
            }
            if !self.is_routable(s) {
                blocked = true;
                return false;
            }
            // core rect
            if s.cllx > x || x > s.curx || s.clly > y || y > s.cury {
                blocked = true;
                return false;
            }
            let width = s.core_width(nd);
            let height = s.core_height(nd);
            let center = s.core_center(nd);
            let safe_x = if width >= wide {
                py_round((x - s.cllx).abs().min((x - s.curx).abs()), nd) >= half_wide
            } else {
                center.0 == x
            };
            let safe_y = if height >= wide {
                py_round((y - s.clly).abs().min((y - s.cury).abs()), nd) >= half_wide
            } else {
                center.1 == y
            };
            if !safe_x || !safe_y {
                blocked = true;
                return false;
            }
            let (xs, ys) = self.safe_pin_values(s);
            let mut xdiff = (x - xs[0]).abs();
            if xs.len() > 1 {
                xdiff = xdiff.min((x - xs[1]).abs());
            }
            let xdiff = py_round(xdiff, nd);
            let mut ydiff = (y - ys[0]).abs();
            if ys.len() > 1 {
                ydiff = ydiff.min((y - ys[1]).abs());
            }
            let ydiff = py_round(ydiff, nd);
            if xdiff == 0.0 && ydiff == 0.0 {
                if s.core_eq(&self.source) || s.core_eq(&self.target) {
                    // Python returns False for the whole check here,
                    // aborting the blockage scan.
                    early_exit = true;
                    return true;
                }
            } else if xdiff < spacing && ydiff < spacing {
                blocked = true;
            }
            false
        });
        if early_exit {
            return false;
        }
        blocked
    }

    /// is_via_blocked with check_blockages=False: via tree test at `node`.
    fn is_via_blocked_at(&self, node: &Node) -> bool {
        if self.graph_vias.is_empty() {
            return false;
        }
        let tree = match &self.via_tree {
            Some(t) => t,
            None => return false,
        };
        let nd = self.nd();
        let (x, y) = (node.x, node.y);
        let mut blocked = false;
        tree.iterate_point(x, y, |leaf| {
            let v = &self.graph_vias[leaf];
            if v.llx > x || x > v.urx || v.lly > y || y > v.ury {
                return false;
            }
            let cx = py_round(0.5 * (v.llx + v.urx), nd);
            let cy = py_round(0.5 * (v.lly + v.ury), nd);
            if cx != x || cy != y {
                blocked = true;
                return true;
            }
            false
        });
        blocked
    }

    /// graph.generate_graph_nodes: create the Hanan grid, mark blocked
    /// nodes, and connect nearest live neighbors in the exact same order
    /// as the Python implementation (order determines A* tie-breaking).
    fn generate_graph_nodes(&mut self, x_values: &[f64], y_values: &[f64]) {
        let y_len = y_values.len();
        let n = x_values.len() * y_len * 2;
        self.nodes = Vec::with_capacity(n);
        for &x in x_values {
            for &y in y_values {
                for z in 0..2u8 {
                    self.nodes.push(Node { x, y, z });
                }
            }
        }
        self.node_ids = (0..n as u32).collect();
        self.neighbors = vec![Vec::new(); n];
        self.removed = (0..n).map(|i| self.is_node_blocked(&self.nodes[i])).collect();

        let mut previous_x: Vec<[i64; 2]> = vec![[-1, -1]; y_len]; // node index or -1
        let mut previous_x_positions: Vec<[i64; 2]> = vec![[-1, -1]; y_len];
        for x_index in 0..x_values.len() {
            let mut previous_y: [i64; 2] = [-1, -1];
            let mut previous_y_positions: [i64; 2] = [-1, -1];
            let x_offset = x_index * y_len * 2;
            for y_index in 0..y_len {
                let i = x_offset + y_index * 2;
                let b0 = i;
                let b1 = i + 1;

                let down0 = if !self.removed[b0] { previous_y[0] } else { -1 };
                let down1 = if !self.removed[b1] { previous_y[1] } else { -1 };
                if previous_y_positions[0] >= previous_y_positions[1] {
                    self.try_connect(b0, down0);
                    self.try_connect(b1, down1);
                } else {
                    self.try_connect(b1, down1);
                    self.try_connect(b0, down0);
                }

                let left0 = if !self.removed[b0] { previous_x[y_index][0] } else { -1 };
                let left1 = if !self.removed[b1] { previous_x[y_index][1] } else { -1 };
                if previous_x_positions[y_index][0] >= previous_x_positions[y_index][1] {
                    self.try_connect(b0, left0);
                    self.try_connect(b1, left1);
                } else {
                    self.try_connect(b1, left1);
                    self.try_connect(b0, left0);
                }

                if !self.removed[b0] {
                    previous_y[0] = b0 as i64;
                    previous_y_positions[0] = y_index as i64;
                    previous_x[y_index][0] = b0 as i64;
                    previous_x_positions[y_index][0] = x_index as i64;
                }
                if !self.removed[b1] {
                    previous_y[1] = b1 as i64;
                    previous_y_positions[1] = y_index as i64;
                    previous_x[y_index][1] = b1 as i64;
                    previous_x_positions[y_index][1] = x_index as i64;
                }

                if !self.removed[b0]
                    && !self.removed[b1]
                    && !self.is_via_blocked_at(&self.nodes[b1])
                {
                    self.neighbors[b0].push(b1 as u32);
                    self.neighbors[b1].push(b0 as u32);
                }
            }
        }

        // remove_blocked_nodes: drop marked nodes' adjacency.
        for i in 0..n {
            if self.removed[i] {
                self.neighbors[i].clear();
            }
        }
        for i in 0..n {
            self.neighbors[i].retain(|&j| !self.removed[j as usize]);
        }
    }

    fn try_connect(&mut self, base: usize, other: i64) {
        if other < 0 {
            return;
        }
        let other = other as usize;
        if !self.is_probe_blocked(&self.nodes[base], &self.nodes[other]) {
            self.neighbors[base].push(other as u32);
            self.neighbors[other].push(base as u32);
        }
    }

    /// graph.inside_shape for save_end_nodes.
    fn inside_shape(&self, node: &Node, s: &ShapeIn) -> bool {
        if node.z as i8 != s.zindex {
            return false;
        }
        node.x <= s.curx.max(s.cllx)
            && node.x >= s.curx.min(s.cllx)
            && node.y <= s.cury.max(s.clly)
            && node.y >= s.cury.min(s.clly)
    }

    fn save_end_nodes(&mut self) {
        let source = self.source;
        let target = self.target;
        for i in 0..self.nodes.len() {
            if self.removed[i] {
                continue;
            }
            let node = self.nodes[i];
            if self.inside_shape(&node, &source) {
                self.source_nodes.push(i as u32);
            } else if self.inside_shape(&node, &target) {
                self.target_nodes.push(i as u32);
            }
        }
    }

    fn direction(a: &Node, b: &Node) -> (bool, bool) {
        (a.x == b.x, a.y == b.y)
    }

    fn edge_cost(&self, a: &Node, b: &Node, prev: Option<&Node>) -> f64 {
        let is_vertical = a.x == b.x;
        let mut layer_dist = (a.x - b.x).abs() + (a.y - b.y).abs();
        if is_vertical != (a.z != 0) {
            layer_dist *= 4.0;
        }
        if let Some(p) = prev {
            if Self::direction(a, p) != Self::direction(a, b) {
                layer_dist += self.ctx.tech.grid;
            }
        }
        let via_dist = (a.z as i32 - b.z as i32).abs() as f64 * 2.0;
        layer_dist + via_dist
    }

    /// graph._make_heuristic: precomputed per search.
    fn make_heuristic(&self) -> Heuristic {
        let targets: Vec<(f64, f64, f64)> = self
            .target_nodes
            .iter()
            .map(|&i| {
                let n = &self.nodes[i as usize];
                (n.x, n.y, n.z as f64)
            })
            .collect();
        let mut set = targets.clone();
        set.sort_by(|a, b| {
            a.0.total_cmp(&b.0)
                .then(a.1.total_cmp(&b.1))
                .then(a.2.total_cmp(&b.2))
        });
        set.dedup();
        let mut xs: Vec<f64> = set.iter().map(|t| t.0).collect();
        xs.sort_by(|a, b| a.total_cmp(b));
        xs.dedup();
        let mut ys: Vec<f64> = set.iter().map(|t| t.1).collect();
        ys.sort_by(|a, b| a.total_cmp(b));
        ys.dedup();
        let mut zs: Vec<f64> = set.iter().map(|t| t.2).collect();
        zs.sort_by(|a, b| a.total_cmp(b));
        zs.dedup();
        let is_cartesian = !targets.is_empty() && set.len() == xs.len() * ys.len() * zs.len();
        Heuristic { targets, set, xs, ys, zs, is_cartesian }
    }

    /// graph.find_shortest_path (A* with (f, id) heap ordering).
    fn find_shortest_path(&mut self) -> Option<Vec<(f64, f64, u8)>> {
        let n = self.nodes.len();
        let mut is_target = vec![false; n];
        for &t in &self.target_nodes {
            is_target[t as usize] = true;
        }
        let heuristic = self.make_heuristic();
        let mut h_cache: Vec<f64> = vec![f64::NAN; n];
        macro_rules! h {
            ($g:expr, $i:expr) => {{
                if h_cache[$i].is_nan() {
                    let node = &$g.nodes[$i];
                    h_cache[$i] = heuristic.distance(node.x, node.y, node.z as f64);
                }
                h_cache[$i]
            }};
        }

        let mut queue: BinaryHeap<HeapEntry> = BinaryHeap::new();
        let mut closed = vec![false; n];
        let mut came_from: Vec<i64> = vec![-1; n];
        let mut g_scores: Vec<f64> = vec![f64::INFINITY; n];
        let mut has_g: Vec<bool> = vec![false; n];

        for &s in &self.source_nodes.clone() {
            let i = s as usize;
            g_scores[i] = 0.0;
            has_g[i] = true;
            let f = h!(self, i);
            queue.push(HeapEntry { f, id: self.node_ids[i], index: i as u32 });
        }

        while let Some(entry) = queue.pop() {
            let cur = entry.index as usize;
            if closed[cur] {
                continue;
            }
            closed[cur] = true;

            if is_target[cur] {
                let mut path = Vec::new();
                let mut c = cur as i64;
                while c >= 0 {
                    let node = &self.nodes[c as usize];
                    path.push((node.x, node.y, node.z));
                    c = came_from[c as usize];
                }
                path.reverse();
                return Some(path);
            }

            let prev = if came_from[cur] >= 0 {
                Some(self.nodes[came_from[cur] as usize])
            } else {
                None
            };

            for k in 0..self.neighbors[cur].len() {
                let nb = self.neighbors[cur][k] as usize;
                let tentative = self.edge_cost(&self.nodes[cur], &self.nodes[nb], prev.as_ref())
                    + g_scores[cur];
                if !has_g[nb] || tentative < g_scores[nb] {
                    came_from[nb] = cur as i64;
                    g_scores[nb] = tentative;
                    has_g[nb] = true;
                    let f = tentative + h!(self, nb);
                    queue.push(HeapEntry { f, id: self.node_ids[nb], index: nb as u32 });
                }
            }
        }
        None
    }
}

struct Heuristic {
    targets: Vec<(f64, f64, f64)>,
    set: Vec<(f64, f64, f64)>,
    xs: Vec<f64>,
    ys: Vec<f64>,
    zs: Vec<f64>,
    is_cartesian: bool,
}

impl Heuristic {
    fn distance(&self, cx: f64, cy: f64, cz: f64) -> f64 {
        if self.set.len() == 1 {
            let t = self.set[0];
            (t.0 - cx).abs() + (t.1 - cy).abs() + (t.2 - cz).abs()
        } else if self.is_cartesian {
            nearest_axis(cx, &self.xs) + nearest_axis(cy, &self.ys) + nearest_axis(cz, &self.zs)
        } else {
            let mut best = f64::INFINITY;
            for t in &self.targets {
                let d = (t.0 - cx).abs() + (t.1 - cy).abs() + (t.2 - cz).abs();
                if d < best {
                    best = d;
                }
            }
            best
        }
    }
}

fn nearest_axis(value: f64, values: &[f64]) -> f64 {
    let index = values.partition_point(|v| *v < value);
    if index == 0 {
        return (values[0] - value).abs();
    }
    if index == values.len() {
        return (values[values.len() - 1] - value).abs();
    }
    (values[index - 1] - value).abs().min((values[index] - value).abs())
}

struct HeapEntry {
    f: f64,
    id: u32,
    index: u32,
}

impl PartialEq for HeapEntry {
    fn eq(&self, other: &Self) -> bool {
        self.f == other.f && self.id == other.id
    }
}
impl Eq for HeapEntry {}
impl PartialOrd for HeapEntry {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}
impl Ord for HeapEntry {
    fn cmp(&self, other: &Self) -> Ordering {
        // BinaryHeap is a max-heap; reverse for min-(f, id) ordering.
        other
            .f
            .total_cmp(&self.f)
            .then_with(|| other.id.cmp(&self.id))
    }
}

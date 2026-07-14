use openram_core::{RouteContext, ShapeIn, Tech};
use pyo3::prelude::*;

/// One shape crossing the boundary:
/// ((llx, lly, urx, ury, cllx, clly, curx, cury),
///  (name, lpp, zindex, same0, same1, layer_num, purpose))
type PyShape = (
    (f64, f64, f64, f64, f64, f64, f64, f64),
    (u32, u32, i8, bool, bool, i16, i32),
);

fn to_shape(t: &PyShape) -> ShapeIn {
    let (r, m) = t;
    ShapeIn {
        llx: r.0,
        lly: r.1,
        urx: r.2,
        ury: r.3,
        cllx: r.4,
        clly: r.5,
        curx: r.6,
        cury: r.7,
        name: m.0,
        lpp: m.1,
        zindex: m.2,
        same_route_lpp: [m.3, m.4],
        layer_num: m.5,
        purpose: m.6,
    }
}

/// Reusable routing context: build once per router.route() call, then
/// route each source/target pair against it.
#[pyclass]
struct Router {
    ctx: RouteContext,
}

#[pymethods]
impl Router {
    #[new]
    fn new(
        grid: f64,
        ndigits: usize,
        track_wire: f64,
        track_space: f64,
        half_wire: f64,
        region_spacing: f64,
    ) -> Self {
        Router {
            ctx: RouteContext {
                tech: Tech {
                    grid,
                    ndigits,
                    track_wire,
                    track_space,
                    half_wire,
                    region_spacing,
                },
                blockages: Vec::new(),
                vias: Vec::new(),
            },
        }
    }

    fn set_blockages(&mut self, shapes: Vec<PyShape>) {
        self.ctx.blockages = shapes.iter().map(to_shape).collect();
    }

    fn set_vias(&mut self, shapes: Vec<PyShape>) {
        self.ctx.vias = shapes.iter().map(to_shape).collect();
    }

    /// Returns the path as [(x, y, z)], or None if unroutable.
    fn route(
        &self,
        py: Python<'_>,
        source: PyShape,
        target: PyShape,
    ) -> Option<Vec<(f64, f64, u8)>> {
        let ctx = &self.ctx;
        let (s, t) = (to_shape(&source), to_shape(&target));
        py.detach(|| ctx.route(s, t))
    }
}

/// Rust-owned router state for one route() run: blockages, vias, pins,
/// and the per-pair routing, with no per-pair reconversion.
#[pyclass]
struct RouterStore {
    store: openram_core::router_store::RouterStore,
}

#[pymethods]
impl RouterStore {
    #[new]
    fn new(
        grid: f64,
        ndigits: usize,
        track_wire: f64,
        track_space: f64,
        half_wire: f64,
        region_spacing: f64,
    ) -> Self {
        RouterStore {
            store: openram_core::router_store::RouterStore::new(Tech {
                grid,
                ndigits,
                track_wire,
                track_space,
                half_wire,
                region_spacing,
            }),
        }
    }

    fn intern_name(&mut self, name: &str) -> u32 {
        self.store.intern_name(name)
    }

    fn intern_lpp(&mut self, lpp: &str) -> u32 {
        self.store.intern_lpp(lpp)
    }

    fn add_pin(&mut self, pin: PyShape) {
        self.store.add_pin(to_shape(&pin));
    }

    fn append_blockage(&mut self, shape: PyShape) {
        self.store.append_blockage(to_shape(&shape));
    }

    fn find_blockages_layer(&mut self, py: Python<'_>, layer_num: i16, shapes: Vec<PyShape>) {
        let converted: Vec<_> = shapes.iter().map(to_shape).collect();
        let store = &mut self.store;
        py.detach(|| store.find_blockages_layer(layer_num, converted))
    }

    fn find_vias(&mut self, py: Python<'_>, shapes: Vec<(PyShape, PyShape)>) {
        let converted: Vec<_> = shapes
            .iter()
            .map(|(raw, inflated)| (to_shape(raw), to_shape(inflated)))
            .collect();
        let store = &mut self.store;
        py.detach(|| store.find_vias(converted))
    }

    fn convert_vias(&mut self, py: Python<'_>) {
        let store = &mut self.store;
        py.detach(|| store.convert_vias())
    }

    fn convert_blockages(&mut self, py: Python<'_>) {
        let store = &mut self.store;
        py.detach(|| store.convert_blockages())
    }

    fn route(
        &self,
        py: Python<'_>,
        source: PyShape,
        target: PyShape,
    ) -> Option<Vec<(f64, f64, u8)>> {
        let (s, t) = (to_shape(&source), to_shape(&target));
        let store = &self.store;
        py.detach(|| store.route(s, t))
    }

    fn blockage_len(&self) -> usize {
        self.store.blockages.len()
    }

    fn via_len(&self) -> usize {
        self.store.vias.len()
    }
}

/// Spice netlist model + serializer (pilot for Rust-owned netlists).
#[pyclass]
struct NetlistDb {
    db: openram_core::netlist::NetlistDb,
}

#[pymethods]
impl NetlistDb {
    #[new]
    fn new() -> Self {
        NetlistDb {
            db: openram_core::netlist::NetlistDb::default(),
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn add_module(
        &mut self,
        name: &str,
        cell_name: &str,
        no_instances: bool,
        spice_text: Option<String>,
        lvs_text: Option<String>,
        pins: Vec<(String, String)>,
        comments: Vec<String>,
        spice_device: Option<String>,
        lvs_device: Option<String>,
        trim_insts: Vec<String>,
    ) -> usize {
        let id = self.db.modules.len();
        self.db.modules.push(openram_core::netlist::NlModule {
            name: name.to_string(),
            cell_name: cell_name.to_string(),
            no_instances,
            spice_text,
            lvs_text,
            pins,
            comments,
            spice_device,
            lvs_device,
            children: Vec::new(),
            insts: Vec::new(),
            trim_insts: trim_insts.into_iter().collect(),
        });
        id
    }

    fn set_children(&mut self, id: usize, children: Vec<usize>) {
        self.db.modules[id].children = children;
    }

    fn add_inst(&mut self, id: usize, name: &str, module: usize, conns: Vec<String>, has_pins: bool) {
        self.db.modules[id].insts.push(openram_core::netlist::NlInst {
            name: name.to_string(),
            module,
            conns,
            has_pins,
        });
    }

    fn set_top(&mut self, id: usize) {
        self.db.top = Some(id);
    }

    #[pyo3(signature = (path, lvs, trim, header=None))]
    fn write_spice(&self, py: Python<'_>, path: &str, lvs: bool, trim: bool,
                   header: Option<&str>) -> PyResult<()> {
        let text = py.detach(|| self.db.write_spice(lvs, trim, header));
        std::fs::write(path, text)
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(e.to_string()))
    }
}

/// CPython round() parity check hook for tests.
#[pyfunction]
fn snap(x: f64, ndigits: usize) -> f64 {
    openram_core::snap::py_round(x, ndigits)
}

/// GDSII layout reader + hierarchy flattener (gdsMill VlsiLayout subset).
#[pyclass]
struct GdsLayout {
    layout: openram_core::gds::Layout,
}

#[pymethods]
impl GdsLayout {
    #[new]
    fn new(path: &str) -> PyResult<Self> {
        let data = std::fs::read(path)
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(e.to_string()))?;
        let layout = openram_core::gds::Layout::parse(&data)
            .map_err(pyo3::exceptions::PyValueError::new_err)?;
        Ok(GdsLayout { layout })
    }

    /// Empty layout for direct in-memory export from Python.
    #[staticmethod]
    fn empty(user_unit: f64, meter_unit: f64) -> GdsLayout {
        GdsLayout {
            layout: openram_core::gds::Layout::new_empty(user_unit, meter_unit),
        }
    }

    /// Serialize to a GDSII file (gds2writer-compatible bytes).
    /// dates: 12 shorts filling BGNLIB and every BGNSTR.
    fn write_gds(
        &self,
        py: Python<'_>,
        path: &str,
        dates: [i16; 12],
        library_name: &str,
        gds_version: i16,
    ) -> PyResult<()> {
        let bytes = py.detach(|| self.layout.write_gds(&dates, library_name, gds_version));
        std::fs::write(path, bytes)
            .map_err(|e| pyo3::exceptions::PyIOError::new_err(e.to_string()))
    }

    /// Add or replace one structure.
    /// boundaries: (layer, purpose, [x0, y0, x1, y1, ...]) in DB units.
    /// srefs: (child_name, x, y, strans_mirror, mag, angle) where None
    /// means the record is absent (gdsMill "").
    /// texts: (string, layer, purpose, x, y, strans_mirror, mag, angle).
    fn add_structure(
        &mut self,
        name: &str,
        boundaries: Vec<(i16, i16, Vec<f64>)>,
        srefs: Vec<(String, f64, f64, Option<bool>, Option<f64>, Option<f64>)>,
        texts: Vec<(String, i16, i16, f64, f64, Option<bool>, Option<f64>, Option<f64>)>,
    ) {
        let s = openram_core::gds::Structure {
            name: name.to_string(),
            boundaries: boundaries
                .into_iter()
                .map(|(layer, purpose, flat)| openram_core::gds::Boundary {
                    layer,
                    purpose,
                    coords: flat.chunks_exact(2).map(|c| (c[0], c[1])).collect(),
                })
                .collect(),
            srefs: srefs
                .into_iter()
                .map(|(sname, x, y, strans, mag, angle)| openram_core::gds::Sref {
                    sname,
                    xy: (x, y),
                    strans,
                    mag,
                    angle,
                })
                .collect(),
            texts: texts
                .into_iter()
                .map(|(string, layer, purpose, x, y, strans, mag, angle)| {
                    openram_core::gds::Text {
                        layer,
                        purpose,
                        xy: (x, y),
                        string,
                        strans,
                        mag,
                        angle,
                    }
                })
                .collect(),
        };
        self.layout.add_structure(s);
    }

    fn has_structure(&self, name: &str) -> bool {
        self.layout.has_structure(name)
    }

    fn set_root(&mut self, name: &str) {
        self.layout.set_root(name);
    }

    fn root_name(&self) -> Option<String> {
        self.layout
            .root
            .map(|i| self.layout.structures[i].name.clone())
    }

    /// Per-structure element counts (debug/diff aid):
    /// (boundaries_on_layer, total_boundaries, srefs, texts)
    fn struct_counts(&self, name: &str, layer: i16) -> (usize, usize, usize, usize) {
        match self.layout.index.get(name) {
            Some(&i) => {
                let s = &self.layout.structures[i];
                (
                    s.boundaries.iter().filter(|b| b.layer == layer).count(),
                    s.boundaries.len(),
                    s.srefs.len(),
                    s.texts.len(),
                )
            }
            None => (usize::MAX, 0, 0, 0),
        }
    }

    /// measureBoundary: user-unit (llx, lly, urx, ury) of the design.
    fn measure_boundary(&self, py: Python<'_>) -> Option<(f64, f64, f64, f64)> {
        py.detach(|| self.layout.measure_boundary())
    }

    /// Layer numbers in first-seen order (gdsMill layerNumbersInUse).
    fn layers_in_use(&self) -> Vec<i16> {
        self.layout.layers_in_use.clone()
    }

    /// Root-level text labels: (string, layer, purpose, x_db, y_db).
    fn root_texts(&self) -> Vec<(String, i16, i16, f64, f64)> {
        match self.layout.root {
            Some(i) => self.layout.structures[i]
                .texts
                .iter()
                .map(|t| (t.string.clone(), t.layer, t.purpose, t.xy.0, t.xy.1))
                .collect(),
            None => Vec::new(),
        }
    }

    /// Flattened boundaries on (layer, purpose) in user units;
    /// purpose < 0 matches any datatype. Rectangles are 4-value lists,
    /// polygons are flattened point lists (gdsMill-compatible).
    fn get_all_shapes(&self, py: Python<'_>, layer: i16, purpose: i16) -> Vec<Vec<f64>> {
        py.detach(|| self.layout.get_all_shapes(layer, purpose))
    }
}

/// Layout pin groups owned in Rust (hierarchy_layout.pin_map values for
/// the copy_layout_pin bulk path). One store per design module.
#[pyclass]
struct PinStore {
    store: openram_core::pin_store::PinStore,
}

#[pymethods]
impl PinStore {
    #[new]
    fn new(grid: f64) -> Self {
        PinStore {
            store: openram_core::pin_store::PinStore::new(grid),
        }
    }

    /// Intern a layer by name; lpp_str is the exact Python str() of the
    /// tech lpp (pin_sort_key component).
    fn add_layer(&mut self, name: &str, lpp_str: &str) -> u32 {
        self.store.add_layer(name, lpp_str)
    }

    /// Layer table as (name, lpp_str) rows, indexed by layer id.
    fn layer_table(&self) -> Vec<(String, String)> {
        self.store
            .layers
            .iter()
            .map(|l| (l.name.clone(), l.lpp_str.clone()))
            .collect()
    }

    fn new_group(&mut self) -> u32 {
        self.store.new_group()
    }

    /// Master pin list (layer id + post-snap rect) reused per instance.
    fn intern_master(&mut self, pins: Vec<(u32, f64, f64, f64, f64)>) -> u32 {
        let pins = pins
            .into_iter()
            .map(|(layer, llx, lly, urx, ury)| openram_core::pin_store::StorePin {
                layer,
                llx,
                lly,
                urx,
                ury,
            })
            .collect();
        self.store.intern_master(pins)
    }

    /// copy_layout_pin fast path: transform the master under the instance
    /// placement into the group. False means a pin collapsed after
    /// snapping; the caller must fall back to the Python path.
    #[allow(clippy::too_many_arguments)]
    fn copy_pins(
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
        self.store
            .copy_pins(g, master, ox, oy, relx, rely, mirror, rotate)
    }

    /// Whole-array batch: (master, ox, oy, mirror, rotate) per instance
    /// in instance order.
    fn copy_pins_batch(
        &mut self,
        py: Python<'_>,
        g: u32,
        placements: Vec<(u32, f64, f64, u8, u16)>,
        relx: f64,
        rely: f64,
    ) -> bool {
        py.detach(|| self.store.copy_pins_batch(g, &placements, relx, rely))
    }

    /// Cross-store variant: copy another store's group (e.g. the bitcell
    /// array's vdd pins into the bank) under the instance placement.
    /// idmap maps source layer ids into this store.
    #[allow(clippy::too_many_arguments)]
    fn copy_from(
        &mut self,
        src: PyRef<'_, PinStore>,
        src_group: u32,
        idmap: Vec<u32>,
        ox: f64,
        oy: f64,
        relx: f64,
        rely: f64,
        mirror: u8,
        rotate: u16,
        g: u32,
    ) -> bool {
        self.store.copy_from(
            src.store.raw(src_group),
            &idmap,
            ox,
            oy,
            relx,
            rely,
            mirror,
            rotate,
            g,
        )
    }

    /// add_layout_pin into a store-backed group. Returns the snapped rect
    /// for the Python pin handle, or None when the rect collapses (the
    /// caller falls back to Python for the exact error behavior).
    fn add_pin(
        &mut self,
        g: u32,
        layer: u32,
        ox: f64,
        oy: f64,
        w: f64,
        h: f64,
    ) -> Option<(f64, f64, f64, f64)> {
        self.store.add_pin(g, layer, ox, oy, w, h)
    }

    fn group_len(&self, g: u32) -> usize {
        self.store.group_len(g)
    }

    fn group_rev(&self, g: u32) -> u64 {
        self.store.group_rev(g)
    }

    fn group_bounds(&self, g: u32) -> Option<(f64, f64, f64, f64)> {
        self.store.bounds(g)
    }

    /// (layer id, llx, lly, width, height, cx, cy) per pin in emission
    /// order (pin_sort_key sorted when deterministic).
    fn emit(&self, py: Python<'_>, g: u32, deterministic: bool)
            -> Vec<(u32, f64, f64, f64, f64, f64, f64)> {
        py.detach(|| self.store.emit(g, deterministic))
    }

    /// (layer id, llx, lly, urx, ury) per pin in insertion order, for
    /// materializing Python pin_layout handles.
    fn materialize(&self, g: u32) -> Vec<(u32, f64, f64, f64, f64)> {
        self.store
            .raw(g)
            .iter()
            .map(|p| (p.layer, p.llx, p.lly, p.urx, p.ury))
            .collect()
    }
}

#[pymodule]
fn openram_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Router>()?;
    m.add_class::<RouterStore>()?;
    m.add_class::<NetlistDb>()?;
    m.add_class::<GdsLayout>()?;
    m.add_class::<PinStore>()?;
    m.add_function(wrap_pyfunction!(snap, m)?)?;
    Ok(())
}

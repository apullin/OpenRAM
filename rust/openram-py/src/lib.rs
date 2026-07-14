use openram_core::{RouteContext, ShapeIn, Tech};
use pyo3::prelude::*;

/// One shape crossing the boundary:
/// ((llx, lly, urx, ury, cllx, clly, curx, cury), (name, lpp, zindex, same0, same1))
type PyShape = (
    (f64, f64, f64, f64, f64, f64, f64, f64),
    (u32, u32, i8, bool, bool),
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

    /// Flattened boundaries on (layer, purpose) in user units;
    /// purpose < 0 matches any datatype.
    fn get_all_shapes(&self, py: Python<'_>, layer: i16, purpose: i16) -> Vec<(f64, f64, f64, f64)> {
        py.detach(|| {
            self.layout
                .get_all_shapes(layer, purpose)
                .iter()
                .map(|r| (r.llx, r.lly, r.urx, r.ury))
                .collect()
        })
    }
}

#[pymodule]
fn openram_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Router>()?;
    m.add_class::<GdsLayout>()?;
    m.add_function(wrap_pyfunction!(snap, m)?)?;
    Ok(())
}

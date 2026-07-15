// See LICENSE for licensing information.
//
// Rust port of OpenRAM's Hanan-grid graph router kernel
// (compiler/router/graph.py, bbox_node.py, graph_node.py).
// The algorithms and tie-breaking mirror the Python implementation
// exactly so that routes are reproducible across backends.

pub mod bbox_tree;
pub mod gds;
pub mod gds_writer;
pub mod graph;
pub mod netlist;
pub mod pin_store;
pub mod router_store;
pub mod shape;
pub mod snap;

pub use graph::{RouteContext, Tech};
pub use shape::ShapeIn;

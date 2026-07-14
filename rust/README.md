# OpenRAM Rust kernels

Rust ports of OpenRAM's compute-heavy kernels, exposed to Python through
PyO3 as the `openram_rs` module.

## Layout

- `openram-core` — pure Rust library: CPython-compatible grid snapping,
  the flattened bbox tree, and the Hanan-grid routing graph + A* search
  (ports of `compiler/router/{graph,bbox_node,graph_node}.py`).
- `openram-py` — PyO3 bindings (`openram_rs` extension module).

## Build

```sh
cd rust && cargo build --release
```

`compiler/router/rust_router.py` auto-loads
`rust/target/release/libopenram_rs.so`, so no install step is needed for
development. The router uses the Rust kernel when `OPTS.use_rust_router`
is true (the default) and the module is loadable; otherwise it falls back
to the pure-Python `graph` silently.

## Fidelity

The kernel is a faithful port, not a reimplementation: node creation
order, neighbor insertion order, A* heap tie-breaking (f, then node id),
and float snapping (`round(x, n)` ties-to-even, verified against CPython
on 2M samples) all match the Python implementation. Differential compiles
(every route executed on both backends, paths compared exactly) show
identical routes; keep it that way when modifying either side.

Known intentional deviation: none.

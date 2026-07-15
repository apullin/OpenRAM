"""Usage: python3 diff_routes.py <config.py>

Differential compile: every route runs on BOTH the Python graph and the
Rust kernel with identical inputs; paths are compared exactly. The Python
result drives the compile so the reference flow is untouched."""
import sys

import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from common import make_openram_package
make_openram_package()
import openram

stats = {"routes": 0, "match": 0, "mismatch": 0, "both_none": 0}


class diff_graph:
    def __init__(self, rt):
        from openram.router.graph import graph
        from openram.router.rust_router import rust_graph
        self.py = graph(rt)
        self.rs = rust_graph(rt)

    def __getattr__(self, name):
        return getattr(self.py, name)

    def create_graph(self, source, target):
        self.rs.create_graph(source, target)
        self.py.create_graph(source, target)

    def find_shortest_path(self):
        py_path = self.py.find_shortest_path()
        rs_path = self.rs.find_shortest_path()
        stats["routes"] += 1
        a = ([(n.center.x, n.center.y, n.center.z) for n in py_path]
             if py_path else None)
        b = ([(n.center.x, n.center.y, n.center.z) for n in rs_path]
             if rs_path else None)
        if a == b:
            if a is None:
                stats["both_none"] += 1
            stats["match"] += 1
        else:
            stats["mismatch"] += 1
            if stats["mismatch"] <= 3:
                print("ROUTE-DIFF #{}:".format(stats["routes"]),
                      file=sys.stderr)
                print("  py:", a[:6] if a else None, "...", file=sys.stderr)
                print("  rs:", b[:6] if b else None, "...", file=sys.stderr)
        return py_path


def make_graph(self):
    return diff_graph(self)


(OPTS, args) = openram.parse_args()
openram.init_openram(config_file=args[0])
from openram.router.router import router as router_cls
from openram.router.rust_router import load_openram_rs
assert load_openram_rs() is not None, "openram_rs not found"
router_cls.make_graph = make_graph
openram.setup_bitcell()
from openram import sram
s = sram()
openram.end_openram()
print("DIFF-RESULT: {}".format(stats), file=sys.stderr)

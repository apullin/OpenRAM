# See LICENSE for licensing information.
#
# Copyright (c) 2016-2026 Regents of the University of California, Santa Cruz
# All rights reserved.
#
"""
Run independent characterization jobs, in parallel when OPTS.num_threads
is greater than one. Workers are forked so they inherit the loaded design
and tech state; each worker process rebinds OPTS.openram_temp to its own
subdirectory, which isolates every stimulus/simulation/results file that
the characterizer reads or writes.

Keep -j 1 (the default) when dispatching many OpenRAM processes
externally (e.g. wide design-space searches): the simulations then run
serially inside each process exactly as before.
"""
import os

from openram import OPTS

# The worker callable is inherited through fork, never pickled, so it may
# be a bound method closing over arbitrary design state.
_fork_worker = None


def _init_child():
    OPTS.openram_temp = "{0}job{1}/".format(OPTS.openram_temp, os.getpid())
    os.makedirs(OPTS.openram_temp, exist_ok=True)


def _call_fork_worker(job):
    return _fork_worker(job)


def run_jobs(worker, jobs):
    """
    Apply worker to each job, preserving job order in the results.
    Jobs and results must be picklable; the worker must only touch files
    under OPTS.openram_temp.
    """
    global _fork_worker

    jobs = list(jobs)
    num_jobs = min(OPTS.num_threads, len(jobs))
    if num_jobs <= 1:
        return [worker(job) for job in jobs]

    import multiprocessing
    ctx = multiprocessing.get_context("fork")
    _fork_worker = worker
    try:
        with ctx.Pool(num_jobs, initializer=_init_child) as pool:
            return pool.map(_call_fork_worker, jobs)
    finally:
        _fork_worker = None

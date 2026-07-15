#!/usr/bin/env python3
# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.
#
"""
SRAM Compiler

The output files append the given suffixes to the output name:
a spice (.sp) file for circuit simulation
a GDS2 (.gds) file containing the layout
a LEF (.lef) file for preliminary P&R (real one should be from layout)
a Liberty (.lib) file for timing analysis/optimization
"""

import sys
import os
import datetime

# You don't need the next two lines if you're sure that openram package is installed
from common import *
make_openram_package()
import openram

(OPTS, args) = openram.parse_args()

# One or more configuration files: several configs run sequentially in
# this one process, which amortizes interpreter/container startup over
# the whole batch (e.g. design-space sweeps).
if len(args) < 1:
    print(openram.USAGE)
    sys.exit(2)
if len(args) > 1 and (OPTS.output_name != "" or OPTS.output_path != "."):
    # Name/path flags would make every config write to the same place.
    print("-o/-p apply to a single config; batched configs must set "
          "output_name/output_path themselves.")
    sys.exit(2)

# Set top process to openram
OPTS.top_process = 'openram'

# These depend on arguments, so don't load them until now.
from openram import debug


_BATCH_SHARED_OPTIONS = (
    # Technology modules and the verify/characterizer dispatch modules are
    # imported once per Python process.  Do not imply that arbitrary changes
    # to those globals are safe between configs.
    "tech_name",
    "tech_file",
    "use_nix",
    "check_lvsdrc",
    "inline_lvsdrc",
    "use_pex",
    "drc_name",
    "lvs_name",
    "pex_name",
    "analytical_delay",
    "spice_name",
)


def _resolved_batch_config(config_file):
    """Read one config and capture the process-global batch contract."""
    # Preflight must see what each config requests, not simulator/tool names
    # discovered and retained by an earlier compile.  It also must not create
    # a compile log/output directory before the whole batch is accepted.
    openram.read_config(config_file, preserve_tool_state=False,
                        log_config=False)
    shared = tuple((name, getattr(OPTS, name, None))
                   for name in _BATCH_SHARED_OPTIONS)
    destination = os.path.realpath(os.path.join(OPTS.output_path,
                                                 OPTS.output_name))
    return shared, destination


def _validate_batch(config_files):
    """Preflight configs before compiling any member of a batch.

    This deliberately validates only state known to be process-global.  It is
    not a claim that cross-technology or mixed tool-mode batches are supported.
    Configs which cannot be read are reported and omitted so the established
    failure-isolation behavior remains intact for the rest of the batch.
    """
    resolved = []
    failed = []
    for config_file in config_files:
        try:
            shared, destination = _resolved_batch_config(config_file)
        except KeyboardInterrupt:
            raise
        except (Exception, SystemExit, AssertionError) as e:
            failed.append(config_file)
            print("FAILED: {} ({}: {})".format(config_file,
                                               type(e).__name__, e))
        else:
            resolved.append((config_file, shared, destination))

    if not resolved:
        return [], failed

    reference_file, reference_shared, _ = resolved[0]
    reference_shared = dict(reference_shared)
    for config_file, shared_items, _ in resolved[1:]:
        shared = dict(shared_items)
        differences = [name for name in _BATCH_SHARED_OPTIONS
                       if shared[name] != reference_shared[name]]
        if differences:
            details = ", ".join("{}={!r} vs {!r}".format(
                name, reference_shared[name], shared[name])
                for name in differences)
            raise ValueError(
                "batched configs must share technology, verification, and "
                "characterization modes; {} differs from {} ({})".format(
                    config_file, reference_file, details))

    destinations = {}
    for config_file, _, destination in resolved:
        previous = destinations.get(destination)
        if previous is not None:
            raise ValueError(
                "batched configs resolve to the same output destination: "
                "{} and {} -> {}".format(previous, config_file, destination))
        destinations[destination] = config_file

    return [item[0] for item in resolved], failed


def _restore_pristine_options():
    """Undo the final preflight read before normal initialization starts."""
    pristine = getattr(OPTS, "_pristine_options", None)
    if pristine is None:
        return
    OPTS.__dict__.clear()
    OPTS.__dict__.update(pristine)
    OPTS._pristine_options = pristine


def _prepare_batch_config_log():
    """Detach logging from the preceding config before initialization."""
    # Batched -o/-p are rejected above, so these are safe command-line
    # defaults until read_config resolves this member's own destination.
    OPTS.output_name = ""
    OPTS.output_path = "."
    debug.log.create_file = True
    debug.log.setup_output = []


def compile_config(config_file, print_banner):
    initialized = False
    try:
        # Parse config file and set up all the options
        openram.init_openram(config_file=config_file)
        initialized = True

        # Only print banner here so it's not in unit tests
        if print_banner:
            openram.print_banner()

        # Ensure that the right bitcell exists or use the parameterised one
        openram.setup_bitcell()

        # Keep track of running stats
        start_time = datetime.datetime.now()
        openram.print_time("Start", start_time)

        # Output info about this run
        openram.report_status()

        debug.print_raw("Words per row: {}".format(OPTS.words_per_row))

        output_extensions = ["lvs", "sp", "v", "lib", "py", "html", "log"]
        # Only output lef/gds if back-end
        if not OPTS.netlist_only:
            output_extensions.extend(["lef", "gds"])

        output_files = ["{0}{1}.{2}".format(OPTS.output_path,
                                            OPTS.output_name, x)
                        for x in output_extensions]
        debug.print_raw("Output files are: ")
        for path in output_files:
            debug.print_raw(path)

        # Create an SRAM (we can also pass sram_config, see documentation/tutorials for details)
        from openram import sram
        s = sram()

        # Output the files for the resulting SRAM
        s.save()
    finally:
        # A failed compile must not leak its temporary files into the next
        # member of the batch.
        if initialized:
            openram.end_openram()
        else:
            # init_openram itself can fail after creating its temp path.  Do
            # only filesystem cleanup here: importing verifier statistics
            # from a partially initialized technology can mask the cause.
            openram.cleanup_paths()

    openram.print_time("End", datetime.datetime.now(), start_time)


failed = []
batch_args = args
if len(args) > 1:
    try:
        batch_args, failed = _validate_batch(args)
    except ValueError as e:
        print("BATCH REJECTED: {}".format(e))
        sys.exit(2)
    finally:
        _restore_pristine_options()

for config_index, config_file in enumerate(batch_args):
    if len(args) == 1:
        # Single config: fail exactly like before.
        compile_config(config_file, print_banner=True)
    else:
        # Batched configs must not take the rest of the batch down with
        # them; init_openram restores pristine state for the next one.
        try:
            _prepare_batch_config_log()
            compile_config(config_file, print_banner=(config_index == 0))
        except KeyboardInterrupt:
            raise
        except (Exception, SystemExit, AssertionError) as e:
            failed.append(config_file)
            print("FAILED: {} ({}: {})".format(config_file,
                                               type(e).__name__, e))

if failed:
    print("{} of {} configs failed: {}".format(len(failed), len(args),
                                               " ".join(failed)))
    sys.exit(1)

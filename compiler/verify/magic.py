# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.
#
"""
This is a DRC/LVS/PEX interface file for magic + netgen.

We include the tech file for SCN4M_SUBM in the tech directory,
that is included in OpenRAM during DRC.
You can use this interactively by appending the magic system path in
your .magicrc file
path sys /Users/mrg/openram/technology/scn3me_subm/tech

We require the version 30 Magic rules which allow via stacking.
We obtained this file from Qflow ( http://opencircuitdesign.com/qflow/index.html )
and include its appropriate license.
"""

import os
import re
import shutil
from openram import debug
from openram import OPTS
from .run_script import *
# Keep track of statistics
num_drc_runs = 0
num_lvs_runs = 0
num_pex_runs = 0


# def filter_gds(cell_name, input_gds, output_gds):
#     """ Run the gds through magic for any layer processing """
#     global OPTS

#     # Copy .magicrc file into temp dir
#     magic_file = OPTS.openram_tech + "tech/.magicrc"
#     if os.path.exists(magic_file):
#         shutil.copy(magic_file, OPTS.openram_temp)
#     else:
#         debug.warning("Could not locate .magicrc file: {}".format(magic_file))


#     run_file = OPTS.openram_temp + "run_filter.sh"
#     f = open(run_file, "w")
#     f.write("#!/bin/sh\n")
#     f.write("{} -dnull -noconsole << EOF\n".format(OPTS.magic_exe[1]))
#     f.write("gds polygon subcell true\n")
#     f.write("gds warning default\n")
#     f.write("gds read {}\n".format(input_gds))
#     f.write("load {}\n".format(cell_name))
#     f.write("cellname delete \\(UNNAMED\\)\n")
#     #f.write("writeall force\n")
#     f.write("select top cell\n")
#     f.write("gds write {}\n".format(output_gds))
#     f.write("quit -noprompt\n")
#     f.write("EOF\n")

#     f.close()
#     os.system("chmod u+x {}".format(run_file))

#     (outfile, errfile, resultsfile) = run_script(cell_name, "filter")


def _write_gds_read_commands(f, cell_name, gds_name):
    """ The GDS read preamble shared by the magic sessions: reading the
    GDS directly is cheap (seconds) and, unlike a shared .mag view,
    involves no cell files or file locks, so independent sessions can
    run concurrently. """
    f.write("drc off\n")
    f.write("set VDD vdd\n")
    f.write("set GND gnd\n")
    f.write("set SUB gnd\n")
    f.write("gds warning default\n")
    try:
        from openram.tech import flatglob
    except ImportError:
        flatglob = []
        f.write("gds readonly true\n")
    for entry in flatglob:
        f.write("gds flatglob " + entry + "\n")
    f.write("gds flatten true\n")
    f.write("gds ordering true\n")
    f.write("gds read {}\n".format(gds_name))
    f.write('puts "Finished reading gds {}"\n'.format(gds_name))
    f.write("load {}\n".format(cell_name))
    f.write('puts "Finished loading cell {}"\n'.format(cell_name))
    f.write("cellname delete \\(UNNAMED\\)\n")


def write_drc_lvs_scripts(cell_name, gds_name, sp_name, final_verification, output_path):
    """ Write independent extraction and DRC scripts that each read the
    GDS themselves (no shared .mag view), so they can run concurrently.
    """
    global OPTS

    full_magic_file = os.environ.get('OPENRAM_MAGICRC', None)
    if not full_magic_file:
        full_magic_file = OPTS.openram_tech + "tech/.magicrc"
    if os.path.exists(full_magic_file):
        shutil.copy(full_magic_file, output_path + "/.magicrc")
    else:
        debug.warning("Could not locate .magicrc file: {}".format(full_magic_file))

    # Extraction session: GDS read + extract + ext2spice for LVS.
    run_file = output_path + "run_extract.sh"
    f = open(run_file, "w")
    f.write("#!/bin/sh\n")
    f.write('export OPENRAM_TECH="{}"\n'.format(os.environ['OPENRAM_TECH']))
    f.write('echo "$(date): Starting extraction using Magic {}"\n'.format(OPTS.drc_exe[1]))
    f.write('\n')
    f.write("{} -dnull -noconsole << EOF\n".format(OPTS.drc_exe[1]))
    _write_gds_read_commands(f, cell_name, gds_name)
    _write_extract_commands(f, cell_name, sp_name, True, final_verification)
    f.write("quit -noprompt\n")
    f.write("EOF\n")
    f.write("magic_retcode=$?\n")
    f.write('echo "$(date): Finished ($magic_retcode) extraction using Magic {}"\n'.format(OPTS.drc_exe[1]))
    f.write("exit $magic_retcode\n")
    f.close()
    os.system("chmod u+x {}".format(run_file))

    # DRC session: GDS read + drc check (same commands as the .mag-view
    # DRC script after its load).
    try:
        from openram.tech import blackbox_cells
    except ImportError:
        blackbox_cells = []
    run_file = output_path + "run_drc.sh"
    f = open(run_file, "w")
    f.write("#!/bin/sh\n")
    f.write('export OPENRAM_TECH="{}"\n'.format(os.environ['OPENRAM_TECH']))
    for blackbox_cell_name in blackbox_cells:
        mag_file = OPTS.openram_tech + "maglef_lib/" + blackbox_cell_name + ".mag"
        debug.check(os.path.isfile(mag_file), "Could not find blackbox cell {}".format(mag_file))
        f.write('cp {0} .\n'.format(mag_file))
    f.write('echo "$(date): Starting DRC using Magic {}"\n'.format(OPTS.drc_exe[1]))
    f.write('\n')
    f.write("{} -dnull -noconsole << EOF\n".format(OPTS.drc_exe[1]))
    _write_gds_read_commands(f, cell_name, gds_name)
    # The read preamble disables the background checker for speed;
    # re-enable it for the check.
    f.write("drc on\n")
    f.write("select top cell\n")
    f.write("expand\n")
    f.write('puts "Finished expanding"\n')
    f.write("drc euclidean on\n")
    # Workaround to address DRC CIF style not loading if 'drc check' is run before catchup
    if OPTS.tech_name=="gf180mcu":
      f.write("drc catchup\n")
    f.write("drc check\n")
    f.write('puts "Finished drc check"\n')
    f.write("drc catchup\n")
    f.write('puts "Finished drc catchup"\n')
    f.write("drc count total\n")
    f.write("quit -noprompt\n")
    f.write("EOF\n")
    f.write("magic_retcode=$?\n")
    f.write('echo "$(date): Finished ($magic_retcode) DRC using Magic {}"\n'.format(OPTS.drc_exe[1]))
    f.write("exit $magic_retcode\n")
    f.close()
    os.system("chmod u+x {}".format(run_file))


def _write_extract_commands(f, cell_name, sp_name, extract, final_verification, pre=""):
    """ The extraction + ext2spice command block shared by the combined
    and split ext scripts. """
    # Extract
    if not sp_name:
        f.write("port makeall\n")
    else:
        f.write("readspice {}\n".format(sp_name))
    # Hack to work around unit scales in SkyWater
    if OPTS.tech_name=="sky130":
        f.write(pre + "extract style ngspice(si)\n")
    if final_verification and OPTS.route_supplies:
        f.write(pre + "extract unique all\n")
    # Coupling capacitances dominate extraction time and are discarded
    # anyway (ext2spice cthresh infinite below); don't compute them.
    f.write(pre + "extract no coupling\n")
    f.write(pre + "extract all\n")
    f.write(pre + "select top cell\n")
    f.write(pre + "feedback why\n")
    f.write('puts "Finished extract"\n')
    # f.write(pre + "ext2spice hierarchy on\n")
    # f.write(pre + "ext2spice scale off\n")
    # lvs exists in 8.2.79, but be backword compatible for now
    # f.write(pre + "ext2spice lvs\n")
    f.write(pre + "ext2spice hierarchy on\n")
    f.write(pre + "ext2spice format ngspice\n")
    f.write(pre + "ext2spice cthresh infinite\n")
    f.write(pre + "ext2spice rthresh infinite\n")
    f.write(pre + "ext2spice renumber off\n")
    f.write(pre + "ext2spice scale off\n")
    f.write(pre + "ext2spice blackbox on\n")
    f.write(pre + "ext2spice subcircuit top on\n")
    f.write(pre + "ext2spice global off\n")

    # Can choose hspice, ngspice, or spice3,
    # but they all seem compatible enough.
    f.write(pre + "ext2spice format ngspice\n")
    f.write(pre + "ext2spice {}\n".format(cell_name))
    f.write(pre + "select top cell\n")
    f.write(pre + "feedback why\n")
    f.write('puts "Finished ext2spice"\n')


def write_drc_script(cell_name, gds_name, extract, final_verification, output_path, sp_name=None):
    """ Write a magic script to perform DRC and optionally extraction. """
    global OPTS

    # Copy .magicrc file into the output directory
    full_magic_file = os.environ.get('OPENRAM_MAGICRC', None)
    if not full_magic_file:
        full_magic_file = OPTS.openram_tech + "tech/.magicrc"

    if os.path.exists(full_magic_file):
        shutil.copy(full_magic_file, output_path + "/.magicrc")
    else:
        debug.warning("Could not locate .magicrc file: {}".format(full_magic_file))

    run_file = output_path + "run_ext.sh"
    f = open(run_file, "w")
    f.write("#!/bin/sh\n")
    f.write('export OPENRAM_TECH="{}"\n'.format(os.environ['OPENRAM_TECH']))
    f.write('echo "$(date): Starting GDS to MAG using Magic {}"\n'.format(OPTS.drc_exe[1]))
    f.write('\n')
    f.write("{} -dnull -noconsole << EOF\n".format(OPTS.drc_exe[1]))
    # Do not run DRC for extraction/conversion
    f.write("drc off\n")
    f.write("set VDD vdd\n")
    f.write("set GND gnd\n")
    f.write("set SUB gnd\n")
    #f.write("gds polygon subcell true\n")
    f.write("gds warning default\n")
    # Flatten the transistors
    # Bug in Netgen 1.5.194 when using this...
    try:
        from openram.tech import blackbox_cells
    except ImportError:
        blackbox_cells = []

    try:
        from openram.tech import flatglob
    except ImportError:
        flatglob = []
        f.write("gds readonly true\n")

    for entry in flatglob:
        f.write("gds flatglob " +entry + "\n")
    # These two options are temporarily disabled until Tim fixes a bug in magic related
    # to flattening channel routes and vias (hierarchy with no devices in it). Otherwise,
    # they appear to be disconnected.
    f.write("gds flatten true\n")
    f.write("gds ordering true\n")
    f.write("gds read {}\n".format(gds_name))
    f.write('puts "Finished reading gds {}"\n'.format(gds_name))
    f.write("load {}\n".format(cell_name))
    f.write('puts "Finished loading cell {}"\n'.format(cell_name))
    f.write("cellname delete \\(UNNAMED\\)\n")
    f.write("writeall force\n")

    if not extract:
        pre = "#"
    else:
        pre = ""

    _write_extract_commands(f, cell_name, sp_name, extract,
                            final_verification, pre)

    f.write("quit -noprompt\n")
    f.write("EOF\n")
    f.write("magic_retcode=$?\n")
    f.write('echo "$(date): Finished ($magic_retcode) GDS to MAG using Magic {}"\n'.format(OPTS.drc_exe[1]))
    f.write("exit $magic_retcode\n")

    f.close()
    os.system("chmod u+x {}".format(run_file))

    run_file = output_path + "run_drc.sh"
    f = open(run_file, "w")
    f.write("#!/bin/sh\n")
    f.write('export OPENRAM_TECH="{}"\n'.format(os.environ['OPENRAM_TECH']))
    # Copy the bitcell mag files if they exist
    for blackbox_cell_name in blackbox_cells:
        mag_file = OPTS.openram_tech + "maglef_lib/" + blackbox_cell_name + ".mag"
        debug.check(os.path.isfile(mag_file), "Could not find blackbox cell {}".format(mag_file))
        f.write('cp {0} .\n'.format(mag_file))

    f.write('echo "$(date): Starting DRC using Magic {}"\n'.format(OPTS.drc_exe[1]))
    f.write('\n')
    f.write("{} -dnull -noconsole << EOF\n".format(OPTS.drc_exe[1]))
    f.write("load {} -dereference\n".format(cell_name))
    f.write('puts "Finished loading cell {}"\n'.format(cell_name))
    f.write("cellname delete \\(UNNAMED\\)\n")
    f.write("select top cell\n")
    f.write("expand\n")
    f.write('puts "Finished expanding"\n')
    f.write("drc euclidean on\n")
    # Workaround to address DRC CIF style not loading if 'drc check' is run before catchup
    if OPTS.tech_name=="gf180mcu":
      f.write("drc catchup\n")
    f.write("drc check\n")
    f.write('puts "Finished drc check"\n')
    f.write("drc catchup\n")
    f.write('puts "Finished drc catchup"\n')
    # This is needed instead of drc count total because it displays
    # some errors that are not "DRC" errors.
    # f.write("puts -nonewline \"Total DRC errors found: \"\n")
    # f.write("puts stdout [drc listall count total]\n")
    f.write("drc count total\n")
    f.write("quit -noprompt\n")
    f.write("EOF\n")
    f.write("magic_retcode=$?\n")
    f.write('echo "$(date): Finished ($magic_retcode) DRC using Magic {}"\n'.format(OPTS.drc_exe[1]))
    f.write("exit $magic_retcode\n")

    f.close()
    os.system("chmod u+x {}".format(run_file))


def _parse_drc_results(cell_name, outfile):
    """ Parse the Magic DRC output for the error count. """
    # Check the result for these lines in the summary:
    # Total DRC errors found: 0
    # The count is shown in this format:
    # Cell replica_cell_6t has 3 error tiles.
    # Cell tri_gate_array has 8 error tiles.
    # etc.
    try:
        f = open(outfile, "r")
    except FileNotFoundError:
        debug.error("Unable to load DRC results file from {}. Is magic set up?".format(outfile), 1)

    results = f.readlines()
    f.close()
    errors=1
    # those lines should be the last 3
    for line in results:
        if "Total DRC errors found:" in line:
            errors = int(re.split(": ", line)[1])
            break
    else:
        debug.error("Unable to find the total error line in Magic output.", 1)


    # always display this summary
    result_str = "DRC Errors {0}\t{1}".format(cell_name, errors)
    if errors > 0:
        for line in results:
            if "error tiles" in line:
                debug.info(1, line.rstrip("\n"))
        debug.warning(result_str)
    else:
        debug.info(1, result_str)

    return errors


def run_drc(cell_name, gds_name, sp_name=None, extract=True, final_verification=False):
    """Run DRC check on a cell which is implemented in gds_name."""

    global num_drc_runs
    num_drc_runs += 1

    write_drc_script(cell_name, gds_name, extract, final_verification, OPTS.openram_temp, sp_name=sp_name)

    (outfile, errfile, resultsfile) = run_script(cell_name, "ext")

    (outfile, errfile, resultsfile) = run_script(cell_name, "drc")

    return _parse_drc_results(cell_name, outfile)


def _hash_gds_masked(h, path):
    """ Hash a GDS stream with the BGNLIB/BGNSTR records skipped: they
    carry modification timestamps, which must not bust the cache. """
    with open(path, "rb") as f:
        data = f.read()
    pos = 0
    n = len(data)
    while pos + 4 <= n:
        ln = int.from_bytes(data[pos:pos + 2], "big")
        if ln < 4:
            break
        if data[pos + 2] not in (0x01, 0x05):
            h.update(data[pos:pos + ln])
        pos += ln


def _verify_cache_key(cell_name, gds_name, sp_name):
    """ The DRC/LVS verdict is a pure function of the layout, the
    netlist, the generated tool scripts (which embed the tool paths and
    every option), and the tech collateral; hash all of them. """
    import hashlib
    h = hashlib.sha256()
    gds_path = (gds_name if os.path.isabs(gds_name)
                else OPTS.openram_temp + gds_name)
    try:
        _hash_gds_masked(h, gds_path)
    except OSError:
        h.update(b"<missing>")
    h.update(b"\0")
    paths = [OPTS.openram_temp + sp_name if not os.path.isabs(sp_name)
             else sp_name,
             OPTS.openram_temp + "run_extract.sh",
             OPTS.openram_temp + "run_drc.sh",
             OPTS.openram_temp + "run_lvs.sh",
             OPTS.openram_temp + "/.magicrc"]
    tech_dir = OPTS.openram_tech + "tech/"
    if os.path.isdir(tech_dir):
        for f in sorted(os.listdir(tech_dir)):
            if f.endswith(".tech") or f == "setup.tcl":
                paths.append(tech_dir + f)
    for p in paths:
        try:
            with open(p, "rb") as f:
                h.update(f.read())
        except OSError:
            h.update(b"<missing>")
        h.update(b"\0")
    return h.hexdigest()


def _verify_cache_dir():
    base = os.environ.get("XDG_CACHE_HOME",
                          os.path.expanduser("~/.cache"))
    return os.path.join(base, "openram", "verify")


def run_drc_lvs(cell_name, gds_name, sp_name, final_verification=False):
    """Run DRC and LVS together. The Magic DRC and Magic extraction
    sessions each read the GDS themselves (cheap) instead of sharing a
    .mag view (whose cell files are lock-contended), so they run fully
    concurrently; Netgen LVS follows the extracted netlist. Verdicts
    are cached keyed on the layout/netlist/scripts/tech content, so
    re-verifying an unchanged design is free (verify_cache=False
    disables it; remove ~/.cache/openram/verify to clear). """

    global num_drc_runs
    global num_lvs_runs
    num_drc_runs += 1
    num_lvs_runs += 1

    write_drc_lvs_scripts(cell_name, gds_name, sp_name, final_verification, OPTS.openram_temp)
    write_lvs_script(cell_name, gds_name, sp_name, final_verification)

    cache_file = None
    if getattr(OPTS, "verify_cache", True):
        import json
        key = _verify_cache_key(cell_name, gds_name, sp_name)
        cache_file = os.path.join(_verify_cache_dir(), key + ".json")
        try:
            with open(cache_file) as f:
                cached = json.load(f)
            debug.info(1, "DRC/LVS verdict for {} cached: {} DRC / {} LVS "
                          "errors ({})".format(cell_name,
                                               cached["drc_errors"],
                                               cached["lvs_errors"],
                                               cache_file))
            return (cached["drc_errors"], cached["lvs_errors"])
        except (OSError, ValueError, KeyError):
            pass

    drc_handle = start_script(cell_name, "drc")
    extract_handle = start_script(cell_name, "extract")
    wait_script(extract_handle)
    # LVS needs the extracted netlist.
    lvs_handle = start_script(cell_name, "lvs")
    (drc_outfile, drc_errfile, drc_resultsfile) = wait_script(drc_handle)
    (lvs_outfile, lvs_errfile, lvs_resultsfile) = wait_script(lvs_handle)

    drc_errors = _parse_drc_results(cell_name, drc_outfile)
    lvs_errors = _parse_lvs_results(cell_name, lvs_resultsfile)

    if cache_file is not None:
        import json
        os.makedirs(_verify_cache_dir(), exist_ok=True)
        tmp_file = cache_file + ".tmp{}".format(os.getpid())
        with open(tmp_file, "w") as f:
            json.dump({"cell_name": cell_name,
                       "drc_errors": drc_errors,
                       "lvs_errors": lvs_errors}, f)
        os.replace(tmp_file, cache_file)

    return (drc_errors, lvs_errors)


def write_lvs_script(cell_name, gds_name, sp_name, final_verification=False, output_path=None):
    """ Write a netgen script to perform LVS. """

    global OPTS

    if not output_path:
        output_path = OPTS.openram_temp

    # Copy setup.tcl file into the output directory
    full_setup_file = os.environ.get('OPENRAM_NETGENRC', None)
    if not full_setup_file:
        full_setup_file = OPTS.openram_tech + "tech/setup.tcl"
    setup_file = os.path.basename(full_setup_file)

    if os.path.exists(full_setup_file):
        # Copy setup.tcl file into temp dir
        shutil.copy(full_setup_file, output_path)

        setup_file_object = open(output_path + "/setup.tcl", 'a')
        setup_file_object.write("# Increase the column sizes for ease of reading long names\n")
        setup_file_object.write("::netgen::format 120\n")

    else:
        setup_file = 'nosetup'

    run_file = output_path + "/run_lvs.sh"
    f = open(run_file, "w")
    f.write("#!/bin/sh\n")
    f.write('export OPENRAM_TECH="{}"\n'.format(os.environ['OPENRAM_TECH']))
    f.write('echo "$(date): Starting LVS using Netgen {}"\n'.format(OPTS.lvs_exe[1]))
    f.write("{} -noconsole << EOF\n".format(OPTS.lvs_exe[1]))
    # f.write("readnet spice {0}.spice\n".format(cell_name))
    # f.write("readnet spice {0}\n".format(sp_name))
    f.write("lvs {{{0}.spice {0}}} {{{1} {0}}} {2} {0}.lvs.report -full -json\n".format(cell_name, sp_name, setup_file))
    f.write("quit\n")
    f.write("EOF\n")
    f.write("magic_retcode=$?\n")
    f.write('echo "$(date): Finished ($magic_retcode) LVS using Netgen {}"\n'.format(OPTS.lvs_exe[1]))
    f.write("exit $magic_retcode\n")
    f.close()
    os.system("chmod u+x {}".format(run_file))


def run_lvs(cell_name, gds_name, sp_name, final_verification=False, output_path=None):
    """Run LVS check on a given top-level name which is
    implemented in gds_name and sp_name. Final verification will
    ensure that there are no remaining virtual conections. """

    global num_lvs_runs
    num_lvs_runs += 1

    if not output_path:
        output_path = OPTS.openram_temp

    write_lvs_script(cell_name, gds_name, sp_name, final_verification)

    (outfile, errfile, resultsfile) = run_script(cell_name, "lvs")

    return _parse_lvs_results(cell_name, resultsfile)


def _parse_lvs_results(cell_name, resultsfile):
    """ Parse the Netgen LVS report for the error count. """

    total_errors = 0

    # check the result for these lines in the summary:
    try:
        f = open(resultsfile, "r")
    except FileNotFoundError:
        debug.error("Unable to load LVS results from {}".format(resultsfile), 1)

    results = f.readlines()
    f.close()
    # Look for the results after the final "Subcircuit summary:"
    # which will be the top-level netlist.
    final_results = []
    for line in reversed(results):
        if "Subcircuit summary:" in line:
            break
        else:
            final_results.insert(0, line)

    # There were property errors in any module.
    test = re.compile("Property errors were found.")
    propertyerrors = list(filter(test.search, results))
    total_errors += len(propertyerrors)

    # Require pins to match?
    # Cell pin lists for pnand2_1.spice and pnand2_1 altered to match.
    # test = re.compile(".*altered to match.")
    # pinerrors = list(filter(test.search, results))
    # if len(pinerrors)>0:
    #     debug.warning("Pins altered to match in {}.".format(cell_name))

    #if len(propertyerrors)>0:
    #    debug.warning("Property errors found, but not checking them.")

    # Netlists do not match.
    test = re.compile("Netlists do not match.")
    incorrect = list(filter(test.search, final_results))
    total_errors += len(incorrect)

    # Netlists match uniquely.
    test = re.compile("match uniquely.")
    uniquely = list(filter(test.search, final_results))

    # Netlists match uniquely.
    test = re.compile("match correctly.")
    correctly = list(filter(test.search, final_results))

    # Top level pins mismatch
    test = re.compile("The top level cell failed pin matching.")
    pins_incorrectly = list(filter(test.search, final_results))

    # Fail if the pins mismatched
    if len(pins_incorrectly) > 0:
        total_errors += 1

    # Fail if they don't match. Something went wrong!
    if len(uniquely) == 0 and len(correctly) == 0:
        total_errors += 1

    if len(uniquely) == 0 and len(correctly) > 0:
        debug.warning("{0}\tLVS matches but not uniquely".format(cell_name))

    if total_errors>0:
        # Just print out the whole file, it is short.
        for e in results:
            debug.info(1,e.strip("\n"))
        debug.error("{0}\tLVS mismatch (results in {1})".format(cell_name,
                                                                resultsfile))
    else:
        debug.info(1, "{0}\tLVS matches".format(cell_name))

    return total_errors


def run_pex(name, gds_name, sp_name, output=None, final_verification=False, output_path=None):
    """Run pex on a given top-level name which is
       implemented in gds_name and sp_name. """

    global num_pex_runs
    num_pex_runs += 1

    if not output_path:
        output_path = OPTS.openram_temp

    os.chdir(output_path)

    if not output_path:
        output_path = OPTS.openram_temp

    if output == None:
        output = name + ".pex.netlist"

    # check if lvs report has been done
    # if not run drc and lvs
    if not os.path.isfile(name + ".lvs.report"):
        run_drc(name, gds_name)
        run_lvs(name, gds_name, sp_name)

    # pex_fix did run the pex using a script while dev orignial method
    # use batch mode.
    # the dev old code using batch mode does not run and is split into functions
    write_script_pex_rule(gds_name, name, sp_name, output)

    (outfile, errfile, resultsfile) = run_script(name, "pex")

    # rename technology models
    pex_nelist = open(output, 'r')
    s = pex_nelist.read()
    pex_nelist.close()
    s = s.replace('pfet', 'p')
    s = s.replace('nfet', 'n')
    f = open(output, 'w')
    f.write(s)
    f.close()

    # also check the output file
    f = open(outfile, "r")
    results = f.readlines()
    f.close()
    out_errors = find_error(results)
    debug.check(os.path.isfile(output), "Couldn't find PEX extracted output.")

    correct_port(name, output, sp_name)
    return out_errors


def write_batch_pex_rule(gds_name, name, sp_name, output):
    """
    The dev branch old batch mode runset
    2. magic can perform extraction with the following:
    #!/bin/sh
    rm -f $1.ext
    rm -f $1.spice
    magic -dnull -noconsole << EOF
    tech load SCN3ME_SUBM.30
    #scalegrid 1 2
    gds rescale no
    gds polygon subcell true
    gds warning default
    gds read $1
    extract
    ext2spice scale off
    ext2spice
    quit -noprompt
    EOF
    """
    pex_rules = drc["xrc_rules"]
    pex_runset = {
        'pexRulesFile': pex_rules,
        'pexRunDir': OPTS.openram_temp,
        'pexLayoutPaths': gds_name,
        'pexLayoutPrimary': name,
        #'pexSourcePath' : OPTS.openram_temp+"extracted.sp",
        'pexSourcePath': sp_name,
        'pexSourcePrimary': name,
        'pexReportFile': name + ".lvs.report",
        'pexPexNetlistFile': output,
        'pexPexReportFile': name + ".pex.report",
        'pexMaskDBFile': name + ".maskdb",
        'cmnFDIDEFLayoutPath': name + ".def",
    }

    # write the runset file
    file = OPTS.openram_temp + "pex_runset"
    f = open(file, "w")
    for k in sorted(pex_runset.keys()):
        f.write("*{0}: {1}\n".format(k, pex_runset[k]))
    f.close()
    return file


def write_script_pex_rule(gds_name, cell_name, sp_name, output):
    global OPTS
    run_file = OPTS.openram_temp + "run_pex.sh"
    f = open(run_file, "w")
    f.write("#!/bin/sh\n")
    f.write('export OPENRAM_TECH="{}"\n'.format(os.environ['OPENRAM_TECH']))
    f.write('echo "$(date): Starting PEX using Magic {}"\n'.format(OPTS.drc_exe[1]))
    f.write("{} -dnull -noconsole << EOF\n".format(OPTS.drc_exe[1]))
    f.write("gds polygon subcell true\n")
    f.write("gds warning default\n")
    f.write("gds read {}\n".format(gds_name))
    f.write("load {}\n".format(cell_name))
    f.write("select top cell\n")
    f.write("expand\n")
    if not sp_name:
        f.write("port makeall\n")
    else:
        f.write("readspice {}\n".format(sp_name))
    f.write("extract\n")
    f.write("ext2sim labels on\n")
    f.write("ext2sim\n")
    f.write("extresist simplify off\n")
    f.write("extresist all\n")
    f.write("ext2spice hierarchy off\n")
    f.write("ext2spice format ngspice\n")
    f.write("ext2spice renumber off\n")
    f.write("ext2spice scale off\n")
    f.write("ext2spice blackbox on\n")
    f.write("ext2spice subcircuit top on\n")
    f.write("ext2spice global off\n")
    f.write("ext2spice extresist on\n")
    f.write("ext2spice {}\n".format(cell_name))
    f.write("quit -noprompt\n")
    f.write("EOF\n")
    f.write("magic_retcode=$?\n")
    f.write("mv {0}.spice {1}\n".format(cell_name, output))
    f.write('echo "$(date): Finished PEX using Magic {}"\n'.format(OPTS.drc_exe[1]))
    f.write("exit $magic_retcode\n")

    f.close()
    os.system("chmod u+x {}".format(run_file))


def find_error(results):
    # Errors begin with "ERROR:"
    test = re.compile("ERROR:")
    stdouterrors = list(filter(test.search, results))
    for e in stdouterrors:
        debug.error(e.strip("\n"))
    out_errors = len(stdouterrors)
    return out_errors


def correct_port(name, output_file_name, ref_file_name):
    pex_file = open(output_file_name, "r")
    contents = pex_file.read()
    # locate the start of circuit definition line
    match = re.search(r'^\.subckt+[^M]*', contents, re.MULTILINE)
    match_index_start = match.start()
    match_index_end = match.end()
    # store the unchanged part of pex file in memory
    pex_file.seek(0)
    part1 = pex_file.read(match_index_start)
    pex_file.seek(match_index_end)
    part2 = pex_file.read()

    bitcell_list = "+ "
    if OPTS.words_per_row:
        for bank in range(OPTS.num_banks):
            for bank in range(OPTS.num_banks):
                row = int(OPTS.num_words / OPTS.words_per_row) - 1
                col = int(OPTS.word_size * OPTS.words_per_row) - 1
                bitcell_list += "bitcell_Q_b{0}_r{1}_c{2} ".format(bank, row, col)
                bitcell_list += "bitcell_Q_bar_b{0}_r{1}_c{2} ".format(bank, row, col)
            for col in range(OPTS.word_size * OPTS.words_per_row):
                for port in range(OPTS.num_r_ports + OPTS.num_w_ports + OPTS.num_rw_ports):
                    bitcell_list += "bl{0}_{1} ".format(bank, col)
                    bitcell_list += "br{0}_{1} ".format(bank, col)
    bitcell_list += "\n"
    control_list = "+ "
    if OPTS.words_per_row:
        for bank in range(OPTS.num_banks):
            control_list += "bank_{}/s_en0".format(bank)
    control_list += '\n'

    part2 = bitcell_list + control_list + part2

    pex_file.close()

    # obtain the correct definition line from the original spice file
    sp_file = open(ref_file_name, "r")
    contents = sp_file.read()
    circuit_title = re.search(".SUBCKT " + str(name) + ".*", contents)
    circuit_title = circuit_title.group()
    sp_file.close()

    # write the new pex file with info in the memory
    output_file = open(output_file_name, "w")
    output_file.write(part1)
    output_file.write(circuit_title + '\n')
    output_file.write(part2)
    output_file.close()


def print_drc_stats():
    debug.info(1, "DRC runs: {0}".format(num_drc_runs))


def print_lvs_stats():
    debug.info(1, "LVS runs: {0}".format(num_lvs_runs))


def print_pex_stats():
    debug.info(1, "PEX runs: {0}".format(num_pex_runs))

# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.
#
"""
Some baseline functions to run scripts.
"""

import os
import subprocess
import time
from openram import debug
from openram import OPTS


def start_script(cell_name, script="lvs"):
    """ Launch a run_*.sh script; returns a handle for wait_script.
    Splitting launch from wait lets independent scripts (e.g. DRC and
    LVS after extraction) run concurrently. """

    echo_cmd_output = OPTS.verbose_level > 1

    cwd = os.getcwd()
    os.chdir(OPTS.openram_temp)
    errfile = "{0}{1}.{2}.err".format(OPTS.openram_temp, cell_name, script)
    outfile = "{0}{1}.{2}.out".format(OPTS.openram_temp, cell_name, script)
    resultsfile = "{0}{1}.{2}.report".format(OPTS.openram_temp, cell_name, script)

    scriptpath = '{0}run_{1}.sh'.format(OPTS.openram_temp, script)

    debug.info(2, "Starting {}".format(scriptpath))
    start = time.time()
    with open(outfile, 'wb') as fo, open(errfile, 'wb') as fe:
        # When this process already runs inside a nix shell, the tools are
        # on PATH; re-entering `nix develop` per script re-evaluates the
        # flake every time for nothing.
        if OPTS.use_nix and "IN_NIX_SHELL" not in os.environ:
            p_cmd = [
                "nix",
                "--extra-experimental-features", "nix-command flakes",
                "develop",
                "--command",
                scriptpath,
            ]
        else:
            p_cmd = [scriptpath]
        p = subprocess.Popen(
                p_cmd, stdout=fo, stderr=fe, cwd=OPTS.openram_temp)

        tails = []
        if echo_cmd_output:
            for f in [outfile, errfile]:
                tails.append(subprocess.Popen([
                    'tail',
                    '-f',                # Follow the output
                    '--pid', str(p.pid), # Close when this pid exits
                    f,
                ]))

    os.chdir(cwd)

    return (p, tails, scriptpath, start, outfile, errfile, resultsfile)


def wait_script(handle):
    """ Wait for a script started with start_script. """

    (p, tails, scriptpath, start, outfile, errfile, resultsfile) = handle

    lastoutput = start
    while p.poll() == None:
        runningfor = time.time() - start
        outputdelta = time.time() - lastoutput
        if outputdelta > 30:
            lastoutput = time.time()
            debug.info(1, "Still running {} ({:.0f} seconds)".format(scriptpath, runningfor))
        # Fine-grained poll: 1s quanta added up to a second of latency
        # per script in pipelined DRC/LVS runs.
        time.sleep(0.05)
    assert p.poll() != None, (p.poll(), p)
    p.wait()

    # Kill the tail commands if they haven't finished.
    for t in tails:
        if t.poll() != None:
            t.kill()
        t.wait()

    debug.info(2, "Finished {} with {}".format(scriptpath, p.returncode))

    return (outfile, errfile, resultsfile)


def run_script(cell_name, script="lvs"):
    """ Run script and create output files. """

    return wait_script(start_script(cell_name, script))

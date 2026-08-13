#!/bin/bash
#
# run_vla_pipeline.slurm
# =======================
# Submit the CASA VLA calibration pipeline (pipeline.recipereducer, recipe
# "procedure_hifv.xml" -- the standard hifv_importdata -> hifv_hanning ->
# hifv_flagdata -> ... -> hifv_exportdata calibration-only recipe used by
# https://science.nrao.edu/facilities/vla/data-processing/pipeline) as a
# batch job, for one SDM dataset given on the command line.
#
# Usage
# -----
#   sbatch [slurm-options] run_vla_pipeline.slurm [options] <path-to-SDM-dataset>
#
#   <path-to-SDM-dataset>       Required (positional, may come before or after
#                                the options below). Path to the unpacked SDM
#                                directory, e.g. TLOW0001/observation.55815.86697627315/
#                                TLOW0001_sb4539976_1.55815.86695445602, or a
#                                .tar archive of one (extracted into the run
#                                dir automatically before the pipeline starts)
#                                (run fix_scan_intents.py on it first -- see below,
#                                only supported for an already-unpacked directory)
#
# SLURM resources (partition/mem/time/cpus/account/...)
# ------------------------------------------------------
# This script is meant to run on any SLURM cluster: it carries only generic
# #SBATCH defaults below (no site-specific partition/account) and relies on
# sbatch's normal precedence -- command-line flags override #SBATCH lines in
# the script -- to adapt to wherever it's submitted. Pass SLURM flags to
# `sbatch` itself, *before* the script name, e.g.:
#   sbatch --partition=<partition> --account=<account> --mem=256G \
#     --time=48:00:00 --cpus-per-task=32 \
#     run_vla_pipeline.slurm --cores 32 --mpi <SDM>
# Most sites require at least --partition (there's no portable default);
# check `sinfo` if you don't know your cluster's partition names. Options
# after the script name (below) are this script's own, unrelated to sbatch's.
#
# Options
# -------
#   -c, --cores N                Overrides --cpus-per-task / OMP_NUM_THREADS
#                                for this run (default: the #SBATCH value below).
#   -p, --procedure FILE          Either a bare recipe name as shipped under
#                                <CASA>/lib/py/lib/python3.*/site-packages/pipeline/recipes/,
#                                e.g. procedure_hifv.xml (default, calibration only)
#                                or procedure_hifv_calimage_cont.xml (cal + imaging);
#                                or a path (relative to the submission directory,
#                                or absolute) to a custom recipe xml of your own.
#   -M, --mpi                    Toggle: run under mpicasa instead of plain casa,
#                                which lets pipeline stages that support it (e.g.
#                                flagging, applycal) parallelise across MPI ranks
#                                instead of just OpenMP threads within one process.
#                                mpicasa -n is set to --cores (1 MPI client rank +
#                                cores-1 server engines), each with OMP_NUM_THREADS=1.
#                                Requires --cores >= 2. Default: off (plain casa).
#   -h, --help                   Print usage and exit.
#
# Examples
# --------
#   sbatch run_vla_pipeline.slurm \
#     /scratch/jradcliffe5/TLOW0001/TLOW0001/observation.55815.86697627315/TLOW0001_sb4539976_1.55815.86695445602
#
#   sbatch run_vla_pipeline.slurm --cores 16 --procedure procedure_hifv_calimage_cont.xml \
#     --mpi /path/to/SDM
#
#   sbatch --partition=gpu --time=48:00:00 --cpus-per-task=32 \
#     run_vla_pipeline.slurm --cores 32 --mpi /path/to/SDM
#
# Each submission gets its own working directory under reduction/<sdm_name>/<jobid>/
# so multiple SDMs (or repeat runs of the same SDM) can be queued/run concurrently
# without clobbering each other's weblog. The exported data products are the one
# exception: the pipeline puts them in reduction/<sdm_name>/products (its
# products_dir default is "../products" relative to the working dir), which every
# run of the same SDM therefore shares and overwrites -- move or rename that
# directory between runs if you need to keep a previous run's products.
#
# NOTE: before running the pipeline on old (pre ~2013) VLA data, fix known-bad
# scan intents first, e.g.:
#   python3 fix_scan_intents.py --apply --recurse <path-to-SDM-dataset>
# (see https://science.nrao.edu/facilities/vla/data-processing/pipeline for why)
#
#SBATCH --job-name=vlapipe
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=128G
#SBATCH --time=2-0:00:00
#SBATCH --output=%x_%j.out
#SBATCH --error=%x_%j.err
# No --partition/--account default on purpose (too site-specific to guess) --
# pass one at submit time, e.g. `sbatch --partition=<partition> ...`, or set
# one here permanently if this copy of the script is pinned to one cluster.
# --mem/--time/--cpus-per-task above are generic starting points; override at
# submit time the same way -- see "SLURM resources" in the usage note above.

set -euo pipefail

# --------------------------------------------------------------------------
# Site-specific paths -- edit if you move the CASA distribution or project.
# --------------------------------------------------------------------------
PROJECT_ROOT="${PROJECT_ROOT:-/scratch/jradcliffe5/TLOW0001}"
CASA_ROOT="${CASA_ROOT:-$PROJECT_ROOT/casa-6.6.6-18-pipeline-2025.1.0.36-py3.10.el8}"
CASA="$CASA_ROOT/bin/casa"
# Batch/SLURM nodes have no X server, but casaplotms (used throughout the
# weblog stages) hard-requires a DISPLAY to be set, or it raises
# "RuntimeError: ERROR: DISPLAY environment variable is not set!" and skips
# that plot. XVFB_BIN points at a virtual framebuffer X server installed
# user-locally via micromamba (no root needed) -- see tools/README (or ask
# Claude) if it's missing. Provisioned with:
#   tools/bin/micromamba create -y -n xvfb -c conda-forge xorg-xvfb-server
XVFB_ENV="${XVFB_ENV:-$PROJECT_ROOT/tools/micromamba_root/envs/xvfb}"
XVFB_BIN="$XVFB_ENV/bin/Xvfb"

usage() {
    echo "Usage: sbatch $0 [options] <path-to-SDM-dataset>" >&2
    echo "Options:" >&2
    echo "  -c, --cores N          OMP_NUM_THREADS / cpus-per-task override (default: \$SLURM_CPUS_PER_TASK)" >&2
    echo "  -p, --procedure FILE   Recipe xml (default: procedure_hifv.xml)" >&2
    echo "  -M, --mpi              Run under mpicasa with --cores MPI server engines (default: off, plain casa)" >&2
    echo "  -h, --help             Show this help" >&2
}

SDM_PATH_RAW=""
N_CORES=""
PROCEDURE="procedure_hifv.xml"
USE_MPI=0

while [ $# -gt 0 ]; do
    case "$1" in
        -c|--cores)
            N_CORES="${2:-}"; shift 2 ;;
        --cores=*)
            N_CORES="${1#*=}"; shift ;;
        -p|--procedure)
            PROCEDURE="${2:-}"; shift 2 ;;
        --procedure=*)
            PROCEDURE="${1#*=}"; shift ;;
        -M|--mpi)
            USE_MPI=1; shift ;;
        -h|--help)
            usage; exit 0 ;;
        --)
            shift
            if [ -n "${1:-}" ]; then SDM_PATH_RAW="$1"; shift; fi
            break ;;
        -*)
            echo "ERROR: unknown option: $1" >&2
            usage
            exit 1 ;;
        *)
            if [ -n "$SDM_PATH_RAW" ]; then
                echo "ERROR: unexpected extra argument: $1" >&2
                usage
                exit 1
            fi
            SDM_PATH_RAW="$1"
            shift ;;
    esac
done

if [ -z "$SDM_PATH_RAW" ]; then
    echo "ERROR: no SDM dataset given." >&2
    usage
    exit 1
fi

SDM_PATH=$(readlink -m "$SDM_PATH_RAW")
N_CORES="${N_CORES:-${SLURM_CPUS_PER_TASK:-1}}"

# Resolve --procedure to an absolute path *now*, while still in the
# submission directory, if it refers to an existing file (e.g. a custom
# recipe given as a relative path). recipereducer resolves a relative
# procedure path against CASA's CWD at launch time, which by then is
# $RUNDIR (see `cd "$RUNDIR"` below) -- not where sbatch was run from -- so
# an unresolved relative path would silently fail to be found there and
# incorrectly fall through to "Procedure not found" against CASA's built-in
# recipes dir. Bare recipe names that aren't files here (e.g. the default
# procedure_hifv.xml, shipped inside CASA) are left untouched so that
# built-in fallback still applies to them.
if [ -f "$PROCEDURE" ]; then
    PROCEDURE=$(readlink -m "$PROCEDURE")
fi

if [ ! -e "$SDM_PATH" ]; then
    echo "ERROR: SDM path does not exist: $SDM_PATH" >&2
    exit 1
fi
if [ ! -x "$CASA" ]; then
    echo "ERROR: casa executable not found/executable at $CASA (check CASA_ROOT)" >&2
    exit 1
fi

# --------------------------------------------------------------------------
# Check the recipe actually exists *before* doing any expensive work (making
# the run dir, extracting a multi-GB SDM tarball, starting MPI/CASA), rather
# than letting recipereducer discover it minutes later and abort with
# "Procedure not found:: <name>". This mirrors _get_processing_procedure()
# in pipeline/recipereducer.py: an existing file wins (already resolved to
# an absolute path above), otherwise the name is looked up in CASA's
# built-in recipes dir.
# --------------------------------------------------------------------------
if [ -z "$PROCEDURE" ]; then
    echo "ERROR: --procedure given with no value" >&2
    usage
    exit 1
fi
shopt -s nullglob
RECIPES_DIRS=("$CASA_ROOT"/lib/py/lib/python3.*/site-packages/pipeline/recipes)
shopt -u nullglob
RECIPES_DIR="${RECIPES_DIRS[0]:-}"
if [ -f "$PROCEDURE" ]; then
    : # custom recipe given as an existing path -- nothing to look up
elif [ -n "$RECIPES_DIR" ] && [ -f "$RECIPES_DIR/$PROCEDURE" ]; then
    : # bare name shipped with CASA -- recipereducer will find it the same way
else
    echo "ERROR: procedure not found: $PROCEDURE" >&2
    echo "       Not a file relative to the submission directory, and not a recipe in" >&2
    if [ -n "$RECIPES_DIR" ]; then
        echo "       $RECIPES_DIR" >&2
        echo "       Recipes available there for the VLA (hifv):" >&2
        for recipe in "$RECIPES_DIR"/procedure_hifv*.xml; do
            [ -f "$recipe" ] && echo "         $(basename "$recipe")" >&2
        done
    else
        echo "       $CASA_ROOT/lib/py/lib/python3.*/site-packages/pipeline/recipes (no such directory -- check CASA_ROOT)" >&2
    fi
    exit 1
fi
if [ ! -x "$XVFB_BIN" ]; then
    echo "ERROR: Xvfb not found/executable at $XVFB_BIN (check XVFB_ENV)" >&2
    exit 1
fi

MPICASA="$CASA_ROOT/bin/mpicasa"
if [ "$USE_MPI" -eq 1 ] && [ ! -x "$MPICASA" ]; then
    echo "ERROR: mpicasa executable not found/executable at $MPICASA (check CASA_ROOT)" >&2
    exit 1
fi

SDM_NAME=$(basename "$SDM_PATH")
SDM_NAME="${SDM_NAME%.tar}"  # SDM given as a .tar file: keep the run dir named after the dataset, not "<name>.tar"
JOBID="${SLURM_JOB_ID:-manual_$(date +%Y%m%d%H%M%S)}"
RUNDIR="$PROJECT_ROOT/reduction/$SDM_NAME/$JOBID"
mkdir -p "$RUNDIR"
cd "$RUNDIR"

# --------------------------------------------------------------------------
# If the SDM was handed over as a .tar archive rather than an already
# unpacked directory, extract it into the run dir now so hifv_importdata
# (which needs a real SDM directory, not a tarball) gets one. Extracting
# into $RUNDIR rather than somewhere shared keeps this run's copy private if
# the same tarball is reduced concurrently by another job.
# --------------------------------------------------------------------------
if [ -f "$SDM_PATH" ] && [[ "$SDM_PATH" == *.tar ]]; then
    EXTRACT_DIR="$RUNDIR/$SDM_NAME"
    echo "   SDM is a .tar archive -- extracting to $EXTRACT_DIR"
    mkdir -p "$EXTRACT_DIR"
    tar -xvf "$SDM_PATH" -C "$EXTRACT_DIR"
    # Some SDM tarballs wrap everything in one top-level directory instead
    # of storing the SDM's files directly at the archive root, which would
    # otherwise leave the real SDM one level too deep; flatten that case so
    # $EXTRACT_DIR always ends up being the SDM directory itself.
    shopt -s nullglob dotglob
    extracted_entries=("$EXTRACT_DIR"/*)
    shopt -u nullglob dotglob
    if [ "${#extracted_entries[@]}" -eq 1 ] && [ -d "${extracted_entries[0]}" ]; then
        inner="${extracted_entries[0]}"
        shopt -s dotglob
        mv "$inner"/* "$EXTRACT_DIR"/
        shopt -u dotglob
        rmdir "$inner"
    fi
    SDM_PATH="$EXTRACT_DIR"
fi

if [ "$USE_MPI" -eq 1 ]; then
    # mpicasa -n takes the *total* rank count (1 MPI client + N-1 server
    # engines), so --cores maps directly onto -n: one rank per requested
    # core. Each rank gets a single OpenMP thread -- CASA's MPI parallelism
    # is across ranks/processes, not threads within a rank.
    if [ "$N_CORES" -lt 2 ]; then
        echo "ERROR: --mpi needs --cores >= 2 (1 client rank + >=1 server engine), got --cores $N_CORES" >&2
        exit 1
    fi
    N_MPI_RANKS="$N_CORES"
    N_MPI_SERVERS=$((N_MPI_RANKS - 1))
    OMP_NUM_THREADS=1
else
    N_MPI_RANKS=1
    OMP_NUM_THREADS="$N_CORES"
fi
export OMP_NUM_THREADS
export MPLBACKEND=Agg

echo "=========================================================="
echo " VLA CASA pipeline run"
echo "   SDM       : $SDM_PATH"
echo "   Procedure : $PROCEDURE"
echo "   Run dir   : $RUNDIR"
echo "   Cores     : $N_CORES"
if [ "$USE_MPI" -eq 1 ]; then
    echo "   MPI       : mpicasa -n $N_MPI_RANKS ($N_MPI_SERVERS server engine(s) + 1 client), OMP_NUM_THREADS=$OMP_NUM_THREADS/rank"
else
    echo "   MPI       : disabled (plain casa)"
fi
echo "   Host      : $(hostname)"
echo "   Started   : $(date)"
echo "=========================================================="

# --------------------------------------------------------------------------
# Driver script: calls pipeline.recipereducer.reduce() non-interactively.
# Written into the run directory (not /tmp) so it's kept alongside the
# weblog/products for that run, for provenance.
# --------------------------------------------------------------------------
DRIVER="$RUNDIR/pipeline_driver.py"
cat > "$DRIVER" <<PYEOF
import sys
import traceback

import pipeline.recipereducer as recipereducer

vis = [r"$SDM_PATH"]
procedure = "$PROCEDURE"

print(f"[pipeline_driver] vis={vis} procedure={procedure}", flush=True)
try:
    context = recipereducer.reduce(vis=vis, procedure=procedure)
except Exception:
    traceback.print_exc()
    sys.exit(1)
print("[pipeline_driver] pipeline run finished.", flush=True)
PYEOF

CASA_LOG="$RUNDIR/casa-pipeline-${JOBID}.log"

# --------------------------------------------------------------------------
# Start a virtual X server so casaplotms weblog plots render instead of
# erroring out with "DISPLAY environment variable is not set!". Picks a
# random, currently-unused display number so multiple pipeline jobs can run
# concurrently on the same node without colliding.
# --------------------------------------------------------------------------
XVFB_LOG="$RUNDIR/xvfb-${JOBID}.log"
XVFB_PID=""
for attempt in $(seq 1 20); do
    cand=$(( (RANDOM % 8900) + 100 ))
    [ -e "/tmp/.X11-unix/X${cand}" ] && continue
    "$XVFB_BIN" ":${cand}" -screen 0 1280x1024x24 -nolisten tcp >"$XVFB_LOG" 2>&1 &
    cand_pid=$!
    sleep 1
    if kill -0 "$cand_pid" 2>/dev/null; then
        XVFB_PID=$cand_pid
        DISPLAY_NUM=$cand
        break
    fi
done
if [ -z "$XVFB_PID" ]; then
    echo "ERROR: could not start Xvfb after multiple attempts (see $XVFB_LOG)" >&2
    exit 1
fi
export DISPLAY=":${DISPLAY_NUM}"
echo "   Xvfb      : DISPLAY=$DISPLAY (pid $XVFB_PID, log $XVFB_LOG)"

cleanup_xvfb() {
    # Note: run under `set -e`, so the final command here must not fail --
    # a killed Xvfb makes `wait` return 143, which (as the EXIT trap's last
    # command) would otherwise silently override the script's real exit
    # status (e.g. the CASA exit code from below) with 143.
    if [ -n "${XVFB_PID:-}" ] && kill -0 "$XVFB_PID" 2>/dev/null; then
        kill "$XVFB_PID" 2>/dev/null
        wait "$XVFB_PID" 2>/dev/null || true
    fi
}
trap cleanup_xvfb EXIT

set +e
if [ "$USE_MPI" -eq 1 ]; then
    # --oversubscribe: outside a real SLURM allocation (e.g. this script run
    # directly rather than via sbatch, or run on a node with fewer cores than
    # --cores requests), PRRTE has no allocation to read slot counts from and
    # falls back to the node's physical core count -- if that's less than
    # N_MPI_RANKS, mpirun hard-errors ("not enough slots") instead of running.
    # Oversubscribing just relaxes that check/binding; it's a no-op when
    # --cores/N_MPI_RANKS actually fits within the allocation.
    "$MPICASA" -n "$N_MPI_RANKS" --oversubscribe "$CASA" --pipeline --nogui --nologger --log2term --logfile "$CASA_LOG" -c "$DRIVER"
else
    "$CASA" --pipeline --nogui --nologger --log2term --logfile "$CASA_LOG" -c "$DRIVER"
fi
status=$?
set -e

# --------------------------------------------------------------------------
# Package the split measurement set(s) into products/, if the recipe
# produced any. hifv_mstransform (when present in the recipe) writes
# <vis>_targets_cont.ms (continuum, TARGET intent only) and, if
# spectral-line spws were identified, <vis>_targets.ms alongside it in
# $RUNDIR -- but hifv_exportdata's exportmses option exports every MS
# registered in the pipeline context (the full raw/calibrated MS included),
# not just these, so it's not used for this. Instead tar each split MS
# found here into products/, matching the .tgz convention exportdata itself
# uses for flagversions/caltables. No-op if the recipe has no mstransform
# stage, or the run failed before reaching products/.
#
# Note the products dir is NOT under $RUNDIR: the pipeline context defaults
# products_dir to "../products" relative to its working directory (see
# Context.products_dir in pipeline/infrastructure/launcher.py), so with
# cwd=$RUNDIR it resolves one level up, to reduction/<sdm_name>/products --
# shared by every job for this SDM rather than per-jobid.
# --------------------------------------------------------------------------
PRODUCTS_DIR="$(dirname "$RUNDIR")/products"
if [ "$status" -eq 0 ] && [ -d "$PRODUCTS_DIR" ]; then
    for split_ms in "$RUNDIR"/*_targets_cont.ms "$RUNDIR"/*_targets.ms; do
        [ -d "$split_ms" ] || continue
        ms_name=$(basename "$split_ms")
        echo "   Packaging split MS: $ms_name -> $PRODUCTS_DIR/${ms_name}.tgz"
        tar -C "$RUNDIR" -czf "$PRODUCTS_DIR/${ms_name}.tgz" "$ms_name"
    done
fi

echo "=========================================================="
echo "   Finished  : $(date)"
echo "   CASA exit : $status"
echo "   Log file  : $CASA_LOG"
echo "   Weblog    : $(find "$RUNDIR" -maxdepth 1 -iname 'pipeline-*' -o -iname 'weblog' 2>/dev/null | head -1)"
echo "   Products  : $PRODUCTS_DIR"
echo "=========================================================="

exit $status

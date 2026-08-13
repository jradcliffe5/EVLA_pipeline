# EVLA pipeline

Batch-job tooling for running the **CASA VLA calibration pipeline**
(`pipeline.recipereducer`) on archival EVLA/VLA SDM datasets on a SLURM
cluster, plus a repair tool for the mis-tagged scan intents that stop old
(pre-~2013) VLA data from calibrating cleanly.

| File | What it does |
| --- | --- |
| [`run_vla_pipeline.slurm`](run_vla_pipeline.slurm) | SLURM submit script: runs one SDM through a pipeline recipe, headless, with per-run working directories |
| [`fix_scan_intents.py`](fix_scan_intents.py) | Diagnoses and repairs bad `Scan.xml` scan intents in an SDM (run this *before* the pipeline on old data) |
| [`procedures/`](procedures/) | Custom recipe XMLs (relaxed-flagging variants of the stock `procedure_hifv.xml`) |

## Requirements

- A **CASA distribution with the VLA pipeline** — developed against
  `casa-6.6.6-18-pipeline-2025.1.0.36-py3.10.el8`. Point `CASA_ROOT` at it.
- **SLURM** (`sbatch`; `mpicasa` from the CASA distribution for `--mpi`).
- **Xvfb**, a virtual X server. `casaplotms` hard-requires `DISPLAY` to be set
  and raises `RuntimeError: ERROR: DISPLAY environment variable is not set!`
  otherwise, which leaves the weblog missing every plot. The submit script
  starts one automatically but **will not run without the binary present** —
  see [Xvfb setup](#xvfb-setup).
- **Python 3** for `fix_scan_intents.py` (standard library only).

## Quick start

```bash
# 1. Check the SDM's scan intents (report only, nothing is written)
python3 fix_scan_intents.py --report /path/to/SDM

# 2. Apply the fixes if the report flags problems (backs up Scan.xml.orig)
python3 fix_scan_intents.py --apply /path/to/SDM

# 3. Submit. SLURM flags go BEFORE the script name, script options after.
sbatch --partition=<partition> --account=<account> \
  run_vla_pipeline.slurm /path/to/SDM
```

Watch the job with `squeue -u $USER`; the job's stdout/stderr land in
`vlapipe_<jobid>.out` / `.err` in the submission directory.

## `run_vla_pipeline.slurm`

```
sbatch [slurm-options] run_vla_pipeline.slurm [options] <path-to-SDM-dataset>
```

`<path-to-SDM-dataset>` is required and positional (before or after the
options). It may be either an unpacked SDM directory or a `.tar` archive of
one — a tarball is extracted into the run directory automatically before the
pipeline starts.

| Option | Meaning |
| --- | --- |
| `-c, --cores N` | Override `--cpus-per-task` / `OMP_NUM_THREADS` for this run |
| `-p, --procedure FILE` | Recipe to run (default: `procedure_hifv.xml`). Either a bare recipe name shipped inside CASA, or a path to your own — see [Recipes](#recipes) |
| `-M, --mpi` | Run under `mpicasa` instead of plain `casa`. Sets `mpicasa -n` to `--cores` (1 client rank + N-1 server engines, `OMP_NUM_THREADS=1` each). Requires `--cores >= 2` |
| `-h, --help` | Usage |

### SLURM resources

The script carries only generic `#SBATCH` defaults (1 node, 16 cpus, 128G,
2 days) and **no partition or account** — those are too site-specific to
guess. Pass them to `sbatch` itself, before the script name; command-line
flags override the `#SBATCH` lines:

```bash
sbatch --partition=<partition> --account=<account> --mem=256G \
  --time=48:00:00 --cpus-per-task=32 \
  run_vla_pipeline.slurm --cores 32 --mpi /path/to/SDM
```

If you don't know your cluster's partition names, check `sinfo`. Pin a
partition/account permanently in the `#SBATCH` block if this copy of the
script only ever runs on one cluster.

### Configuration

Three environment variables override the paths baked into the script (all
have defaults pointing at the original project layout, so set them for any
other install):

| Variable | Default | Purpose |
| --- | --- | --- |
| `PROJECT_ROOT` | `/scratch/jradcliffe5/TLOW0001` | Root under which `reduction/` run directories are created |
| `CASA_ROOT` | `$PROJECT_ROOT/casa-6.6.6-…-py3.10.el8` | CASA distribution (provides `bin/casa`, `bin/mpicasa`) |
| `XVFB_ENV` | `$PROJECT_ROOT/tools/micromamba_root/envs/xvfb` | Environment containing `bin/Xvfb` |

```bash
PROJECT_ROOT=/my/project CASA_ROOT=/opt/casa-6.6.6 sbatch … run_vla_pipeline.slurm …
```

### Output layout

Each submission gets its own working directory, so repeat runs and
concurrent SDMs don't collide:

```
$PROJECT_ROOT/reduction/<sdm_name>/
├── <jobid>/                      # one per submission (the CASA working dir)
│   ├── pipeline-YYYYMMDDTHHMMSS/ # weblog (open html/index.html)
│   ├── casa-pipeline-<jobid>.log # CASA log
│   ├── xvfb-<jobid>.log          # virtual X server log
│   ├── pipeline_driver.py        # the generated driver, kept for provenance
│   ├── <sdm_name>.ms             # the imported/calibrated MS
│   └── <sdm_name>_targets*.ms    # split MS(es), if the recipe runs hifv_mstransform
└── products/                     # exported data products
```

`products/` is the **one exception**: the pipeline's `products_dir` defaults
to `../products` relative to its working directory, so it sits beside the
job directories and is *shared and overwritten* by every run of the same
SDM. Move or rename it between runs if you need to keep a previous run's
products.

It contains the usual pipeline deliverables — weblog tarball, caltables,
final flag versions, calibration apply lists, `casa_pipescript.py` /
`casa_piperestorescript.py`, FITS images, AQUA report — and, when the recipe
includes `hifv_mstransform`, the **split measurement set(s)** tarred up as
`<sdm_name>_targets_cont.ms.tgz` (continuum, TARGET intent only) and
`<sdm_name>_targets.ms.tgz` (spectral line, if line spws were identified).

> The split MS is packaged by this script rather than by
> `hifv_exportdata(exportmses=True)`, because that option exports *every* MS
> registered in the pipeline context — the full calibrated MS included —
> and can't be scoped to just the split.

## Recipes

The default is CASA's stock **`procedure_hifv.xml`** (calibration only);
`procedure_hifv_calimage_cont.xml` (calibration + imaging) is also shipped
inside CASA. Pass either by bare name:

```bash
sbatch … run_vla_pipeline.slurm --procedure procedure_hifv_calimage_cont.xml /path/to/SDM
```

`procedures/` holds custom recipes for datasets the stock ones choke on.
Pass them by path (relative to the submission directory, or absolute — the
script resolves it before CASA changes directory):

```bash
sbatch … run_vla_pipeline.slurm \
  --procedure procedures/procedure_hifv_minimal_flagging.xml /path/to/SDM
```

| Recipe | Difference from stock |
| --- | --- |
| `procedure_hifv_lessflagging.xml` | `hifv_flagdata` with `template=False`, `quack=False` |
| `procedure_hifv_minimal_flagging.xml` | **Omits `hifv_flagdata` entirely**; `hifv_testBPdcals(flagbaddef=False)`; adds `hifv_mstransform` before export to produce a split target MS |

> [!WARNING]
> Both custom recipes were written for one specific dataset (TLOW0001, 2011)
> whose calibrator scans come out ~100% flagged under the stock recipe,
> starving `hifv_testBPdcals` and crashing the run. They deliberately admit
> data that was flagged for real reasons (antennas still settling on source,
> a genuine subreflector fault, shadowing) into the calibration solves.
> Each file's header comment documents what was tested and what the residual
> risk is. **Check the weblog QA scores and the resulting cal tables before
> trusting products from these recipes for science**, and don't reuse them
> on another dataset without re-deriving whether the same relaxation is
> justified.

## `fix_scan_intents.py`

Old VLA data carries scan intents that the modern pipeline no longer
accepts. This script diagnoses — and optionally repairs — two independent
problems in an SDM's `Scan.xml`:

1. **Setup scan tagged `OBSERVE_TARGET`.** The first scan of a scheduling
   block (used only to set attenuators and requantizer gains) was stamped
   `OBSERVE_TARGET` even though its source is a calibrator on every later
   scan. A field cannot carry both `OBSERVE_TARGET` and `CALIBRATE_*`, so
   the pipeline refuses to run. Fixed by retagging to
   `SYSTEM_CONFIGURATION`, which is what modern SDMs use — per
   [NRAO's guidance](https://science.nrao.edu/facilities/vla/data-processing/pipeline/scan-intent-edit-script).

2. **Flux calibrator missing `CALIBRATE_FLUX`.** 2011-era data tags its
   flux-scale calibrator with `CALIBRATE_AMPLI` only. The pipeline's
   `calibratorIntents()` searches for `CALIBRATE_FLUX*` exclusively — the
   old `CALIBRATE_AMPLI`-means-flux convention was removed in 2018 for *all*
   epochs. Left unfixed this isn't a crash: `hifv_fluxboot`'s flux
   bootstrap fails quietly inside a caught exception, silently losing flux
   calibration transfer. Fixed by adding `CALIBRATE_FLUX` alongside the
   existing intent on scans of the standard flux calibrators
   (3C48/3C138/3C147/3C286).

It also flags any non-canonical intent token, and any scan mixing
`OBSERVE_TARGET` with other intents (reported, never auto-fixed — those need
a human).

```bash
# Report only — always run this first
python3 fix_scan_intents.py --report /path/to/SDM

# Scan a whole project tree for every SDM (any directory containing Scan.xml)
python3 fix_scan_intents.py --report --recurse /path/to/project

# Apply, after reviewing the report
python3 fix_scan_intents.py --apply --recurse /path/to/project
```

| Flag | Meaning |
| --- | --- |
| `--report` | Diagnose only (the default when `--apply` is absent) |
| `--apply` | Write the fixes |
| `--recurse` | Treat each path as a tree root and find every `Scan.xml` under it |
| `--setup-intent` | Intent to use for mis-tagged setup scans (default `SYSTEM_CONFIGURATION`) |

Edits are surgical — only the `scanIntent` field (plus `numIntent`,
`calDataType`, `calibrationOnLine` when an intent is added) inside flagged
`<row>` blocks is rewritten, every other byte is left untouched — and a
`Scan.xml.orig` backup is written once before the first edit, so the change
is trivially reversible.

After fixing, confirm with CASA:

```python
importasdm(asdm='/path/to/SDM', vis='check.ms')
listobs(vis='check.ms')   # intents per field/scan should now look right
```

## Xvfb setup

There is usually no system-wide `Xvfb` on a cluster and no root to install
one, so provision it in user space with
[micromamba](https://mamba.readthedocs.io/):

```bash
export MAMBA_ROOT_PREFIX=$PROJECT_ROOT/tools/micromamba_root
micromamba create -y -n xvfb -c conda-forge xorg-xvfb-server
```

The submit script then picks a free display number (so concurrent jobs on
one node don't collide), starts `Xvfb` on it, exports `DISPLAY`, runs CASA,
and kills the server in an `EXIT` trap. Point `XVFB_ENV` elsewhere if you
install it somewhere other than the default above.

## References

- [VLA pipeline (NRAO)](https://science.nrao.edu/facilities/vla/data-processing/pipeline)
- [Scan intent edit script](https://science.nrao.edu/facilities/vla/data-processing/pipeline/scan-intent-edit-script)
- [Observing guide: setup scans](https://science.nrao.edu/facilities/vla/docs/manuals/obsguide/set-up)

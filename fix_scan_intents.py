#!/usr/bin/env python3
"""
fix_scan_intents.py
====================

Diagnose (and optionally repair) VLA SDM-BDF `Scan.xml` scan intents so
that archival EVLA/VLA data -- especially old, pre-~2013 data like the
TLOW0001 datasets -- imports and calibrates cleanly in the CASA VLA
pipeline (hifv_importdata / importasdm), matching what current (2020s)
archived VLA data actually looks like.

Two independent problems are checked for and fixed, both confirmed by
diffing TLOW0001's 2011 Scan.xml against a modern (2025, project 24B-210)
SDM, and by reading the pipeline's own source
(pipeline/hifv/heuristics/vlascanheuristics.py, pipeline/hifv/tasks/...)
in the CASA distribution shipped alongside this project:

1. Setup/attenuator/requantizer-gain scan tagged OBSERVE_TARGET
   -----------------------------------------------------------------
   The first scan of a scheduling block, used only to set the 8/3-bit
   attenuators and requantizer gains, was stamped OBSERVE_TARGET even
   though its source is used purely as a calibrator on every later scan.
   If unfixed, the pipeline refuses to run: a field cannot carry both an
   OBSERVE_TARGET and a CALIBRATE_* intent. NRAO's guidance is to retag
   such scans SYSTEM_CONFIGURATION (the modern "setup intent"); see
     https://science.nrao.edu/facilities/vla/data-processing/pipeline
     https://science.nrao.edu/facilities/vla/docs/manuals/obsguide/set-up
     https://science.nrao.edu/facilities/vla/data-processing/pipeline/scan-intent-edit-script
   Confirmed against the 24B-210 test dataset: its own setup scan (scan 1)
   is *already* tagged SYSTEM_CONFIGURATION at acquisition time.

2. Flux/standard calibrator missing the literal CALIBRATE_FLUX intent
   -----------------------------------------------------------------
   2011-era data (like TLOW0001) tags its flux-scale calibrator (3C286)
   with CALIBRATE_AMPLI only. The 24B-210 modern dataset tags its
   flux-scale calibrator with CALIBRATE_BANDPASS **CALIBRATE_FLUX**.
   Reading pipeline/hifv/heuristics/vlascanheuristics.py directly: the
   live method actually called by hifv_importdata is calibratorIntents()
   (not the legacy calibratorIntentsOld(), which is dead code only used by
   a test/comparison harness). calibratorIntents() searches ONLY for
   "CALIBRATE_FLUX*" -- its in-code comment says: "We used to use
   CALIBRATE_AMPLI rather than FLUX ... Changed April 2018 so that all
   flux scans and fields require FLUX, even prior to 2013." I.e. the old
   CALIBRATE_AMPLI-means-flux-cal convention was removed from the
   pipeline in 2018 and is no longer honoured for *any* epoch.
   Consequence if left unfixed: hifv_vlasetjy still finds 3C286 (it
   matches calibrators by sky position, not intent), but
   msinfo.flux_field_select_string comes back empty ("No flux density
   calibration fields found"), and hifv_fluxboot's fluxscale bootstrap of
   the phase/gain calibrator's flux density silently fails inside a
   caught exception -- not a hard crash, but a quiet loss of flux
   calibration transfer. Fix: add CALIBRATE_FLUX alongside the existing
   CALIBRATE_AMPLI on scans of the standard flux calibrators. The
   standard-source list (3C48/3C138/3C147/3C286) is taken verbatim from
   pipeline/hifv/tasks/setmodel/vlasetjy.py's own standard_sources().

Both fixes only ever rewrite the specific numeric/token fields inside
flagged <row> blocks (scanIntent, and where an intent is added also
numIntent/calDataType/calibrationOnLine) -- every other byte of Scan.xml
is left untouched -- and a `Scan.xml.orig` backup is made once, before
the first edit, so the change is trivially reversible.

Usage
-----
    # Report only (safe, no files touched) - run this first
    python3 fix_scan_intents.py --report /path/to/SDM

    # Same, but scan a whole project tree for every SDM (dir containing Scan.xml)
    python3 fix_scan_intents.py --report --recurse /scratch/jradcliffe5/TLOW0001

    # Apply both fixes after reviewing the report
    python3 fix_scan_intents.py --apply --recurse /scratch/jradcliffe5/TLOW0001

    # After fixing, re-import and check with CASA:
    #   importasdm(asdm='.../<SDM>', vis='...ms')
    #   listobs(vis='...ms')   # confirm intents per field/scan look right
"""

import argparse
import glob
import os
import re
import sys
from collections import defaultdict

# ---------------------------------------------------------------------------
# Canonical VLA/ALMA-style scan-intent vocabulary, as used by the VLA CASA
# pipeline and NRAO's own sdmintentmods.pl editing script. Anything found in
# a Scan.xml that is NOT in this set is flagged as suspect (typo / stale /
# non-standard convention) rather than assumed correct.
# ---------------------------------------------------------------------------
CANONICAL_INTENTS = {
    "OBSERVE_TARGET",
    "OBSERVE_CHECK_SOURCE",
    "SYSTEM_CONFIGURATION",
    "CALIBRATE_FLUX",
    "CALIBRATE_BANDPASS",
    "CALIBRATE_PHASE",
    "CALIBRATE_AMPLI",
    "CALIBRATE_DELAY",
    "CALIBRATE_POINTING",
    "CALIBRATE_POLARIZATION",
    "CALIBRATE_POL_LEAKAGE",
    "CALIBRATE_POL_ANGLE",
    "CALIBRATE_ATMOSPHERE",
    "CALIBRATE_FOCUS",
    "CALIBRATE_FOCUS_X",
    "CALIBRATE_FOCUS_Y",
    "CALIBRATE_SIDEBAND_RATIO",
    "CALIBRATE_WVR",
    "CALIBRATE_ANTENNA_POSITION",
    "CALIBRATE_ANTENNA_PHASE",
    "CALIBRATE_ANTENNA_POINTING_MODEL",
    "CALIBRATE_APPPHASE_ACTIVE",
    "CALIBRATE_APPPHASE_PASSIVE",
    "DO_SKYDIP",
    "MAP_ANTENNA_SURFACE",
    "MAP_PRIMARY_BEAM",
    "MEASURE_RFI",
    "TEST",
    "UNSPECIFIED",
}

# Intents that mark a scan as calibration of *some* kind (used to decide
# whether a source is "really" a calibrator elsewhere in the exec block).
CALIBRATE_PREFIXES = ("CALIBRATE_",)

DEFAULT_SETUP_INTENT = "SYSTEM_CONFIGURATION"

# Sources the pipeline (pipeline/hifv/tasks/setmodel/vlasetjy.py,
# standard_sources()) recognises as absolute flux-density standards by sky
# position. calibratorIntents() (pipeline/hifv/heuristics/vlascanheuristics.py)
# separately requires the literal CALIBRATE_FLUX intent on whichever field
# is meant to carry the flux scale -- this list is only used to decide which
# scans are eligible for the CALIBRATE_FLUX auto-add below.
STANDARD_FLUX_SOURCES = {"3C48", "3C138", "3C147", "3C286"}
FLUX_INTENT = "CALIBRATE_FLUX"

ROW_RE = re.compile(r"<row>.*?</row>", re.S)
FIELD_RE = lambda tag: re.compile(r"<%s>(.*?)</%s>" % (tag, tag), re.S)

F_SCANNUMBER = FIELD_RE("scanNumber")
F_SOURCENAME = FIELD_RE("sourceName")
F_NUMINTENT = FIELD_RE("numIntent")
F_SCANINTENT = FIELD_RE("scanIntent")
F_CALDATATYPE = FIELD_RE("calDataType")
F_CALONLINE = FIELD_RE("calibrationOnLine")


class ScanRow:
    """One <row> of Scan.xml, with byte offsets (into the full file text) of
    every field that adding/replacing an intent might need to touch."""

    def __init__(self, row_text, row_start):
        self.row_text = row_text
        self.row_start = row_start  # offset of row_text within full file
        self.scan_number = int(F_SCANNUMBER.search(row_text).group(1))
        self.source_name = F_SOURCENAME.search(row_text).group(1)

        self._span = {}
        for name, pat in (
            ("numIntent", F_NUMINTENT),
            ("scanIntent", F_SCANINTENT),
            ("calDataType", F_CALDATATYPE),
            ("calibrationOnLine", F_CALONLINE),
        ):
            m = pat.search(row_text)
            self._span[name] = (row_start + m.start(1), row_start + m.end(1), m.group(1))

        self.intent_raw = self._span["scanIntent"][2]
        parts = self.intent_raw.split()
        # format is: <ndim> <count> intent1 intent2 ... intent<count>
        self.header = parts[:2]
        self.intents = parts[2:]

    def edits_replace_intents(self, new_intents):
        """Swap the intent list in place (count unchanged)."""
        start, end, _ = self._span["scanIntent"]
        text = " ".join(self.header + list(new_intents))
        return [(start, end, text)]

    def edits_add_intent(self, new_token):
        """Append one intent, keeping numIntent/calDataType/calibrationOnLine
        counts consistent, matching how NRAO's own sdmintentmods.pl does it."""
        n = int(self.header[1]) + 1
        edits = []

        start, end, _ = self._span["numIntent"]
        edits.append((start, end, str(n)))

        start, end, _ = self._span["scanIntent"]
        edits.append((start, end, " ".join(["1", str(n)] + self.intents + [new_token])))

        start, end, old = self._span["calDataType"]
        vals = old.split()[2:]
        edits.append((start, end, " ".join(["1", str(n)] + vals + ["NONE"])))

        start, end, old = self._span["calibrationOnLine"]
        vals = old.split()[2:]
        edits.append((start, end, " ".join(["1", str(n)] + vals + ["false"])))

        return edits


def parse_scan_xml(path):
    text = open(path, "r").read()
    rows = []
    for m in ROW_RE.finditer(text):
        rows.append(ScanRow(m.group(0), m.start()))
    return text, rows


def classify_sources(rows):
    """Return {source_name: set-of-intent-tokens-seen-across-all-scans}."""
    by_source = defaultdict(set)
    for r in rows:
        by_source[r.source_name].update(r.intents)
    return by_source


def diagnose_setup_conflicts(rows, source_intents):
    """Scans tagged OBSERVE_TARGET whose source is a calibrator elsewhere."""
    conflicts = []
    for r in rows:
        is_target_only = r.intents == ["OBSERVE_TARGET"]
        has_calibrate_elsewhere = any(
            tok.startswith(CALIBRATE_PREFIXES) for tok in source_intents[r.source_name]
        )
        if is_target_only and has_calibrate_elsewhere:
            conflicts.append(r)
        elif "OBSERVE_TARGET" in r.intents and len(r.intents) > 1:
            # target mixed with something else on the same scan -- unusual,
            # flag but do not auto-fix
            conflicts.append(r)
    return conflicts


def diagnose_missing_flux(rows, source_intents):
    """Standard flux-calibrator sources that never carry CALIBRATE_FLUX
    anywhere in this exec block: flag their calibration scans for the
    CALIBRATE_FLUX add-fix."""
    missing = []
    for src in STANDARD_FLUX_SOURCES & set(source_intents):
        if FLUX_INTENT in source_intents[src]:
            continue  # already has it somewhere -- nothing to do
        for r in rows:
            if r.source_name == src and any(tok.startswith(CALIBRATE_PREFIXES) for tok in r.intents):
                missing.append(r)
    return missing


def diagnose_unknown_tokens(rows):
    unknown = set()
    for r in rows:
        for tok in r.intents:
            if tok not in CANONICAL_INTENTS:
                unknown.add((r.scan_number, tok))
    return unknown


def report_sdm(sdm_dir):
    scan_xml = os.path.join(sdm_dir, "Scan.xml")
    if not os.path.isfile(scan_xml):
        print(f"  [skip] no Scan.xml in {sdm_dir}")
        return None
    text, rows = parse_scan_xml(scan_xml)
    source_intents = classify_sources(rows)
    setup_conflicts = diagnose_setup_conflicts(rows, source_intents)
    flux_missing = diagnose_missing_flux(rows, source_intents)
    unknown = diagnose_unknown_tokens(rows)

    print(f"\n=== {sdm_dir} ===")
    print(f"  {len(rows)} scans, {len(source_intents)} sources")
    for src, intents in sorted(source_intents.items()):
        role = "calibrator" if any(i.startswith(CALIBRATE_PREFIXES) for i in intents) else "target"
        print(f"    {src:<15} -> {role:<10} intents seen: {', '.join(sorted(intents))}")

    if unknown:
        print("  ! Non-canonical intent tokens found (check against current VLA "
              "vocabulary, possible typo/stale convention):")
        for scan_number, tok in sorted(unknown):
            print(f"      scan {scan_number}: {tok!r}")

    if setup_conflicts:
        print("  ! Conflicting scans (source has OBSERVE_TARGET on this scan but "
              "CALIBRATE_* elsewhere -> pipeline will reject this field):")
        for r in setup_conflicts:
            fixable = r.intents == ["OBSERVE_TARGET"]
            action = f"-> would set to {DEFAULT_SETUP_INTENT}" if fixable else "-> NEEDS MANUAL FIX (mixed intents)"
            print(f"      scan {r.scan_number:>3} ({r.source_name}): {' '.join(r.intents)}  {action}")
    else:
        print("  OK: no OBSERVE_TARGET/CALIBRATE_* conflicts found.")

    if flux_missing:
        scans = ", ".join(str(r.scan_number) for r in flux_missing)
        src = flux_missing[0].source_name
        print(f"  ! {src} is a standard flux calibrator but never carries {FLUX_INTENT} "
              f"(current pipeline code requires it explicitly -- see module docstring):")
        print(f"      scans {scans} -> would add {FLUX_INTENT}")
    else:
        have = STANDARD_FLUX_SOURCES & set(source_intents)
        if have:
            print(f"  OK: standard flux calibrator(s) {sorted(have)} already carry {FLUX_INTENT}.")

    return {
        "scan_xml": scan_xml,
        "text": text,
        "rows": rows,
        "setup_conflicts": setup_conflicts,
        "flux_missing": flux_missing,
    }


def apply_fix(info, setup_intent=DEFAULT_SETUP_INTENT):
    scan_xml = info["scan_xml"]
    text = info["text"]

    setup_fixable = [r for r in info["setup_conflicts"] if r.intents == ["OBSERVE_TARGET"]]
    flux_fixable = info["flux_missing"]

    if not setup_fixable and not flux_fixable:
        print(f"  [apply] {scan_xml}: nothing auto-fixable")
        return

    backup = scan_xml + ".orig"
    if not os.path.exists(backup):
        with open(backup, "w") as f:
            f.write(text)
        print(f"  [apply] wrote backup {backup}")
    else:
        print(f"  [apply] backup already exists at {backup} (not overwritten)")

    all_edits = []
    for r in setup_fixable:
        edits = r.edits_replace_intents([setup_intent])
        all_edits.extend(edits)
        print(f"  [apply] scan {r.scan_number} ({r.source_name}): "
              f"setup intent '{r.intent_raw}' -> '{edits[0][2]}'")
    for r in flux_fixable:
        edits = r.edits_add_intent(FLUX_INTENT)
        all_edits.extend(edits)
        scan_intent_text = edits[1][2]  # (numIntent, scanIntent, calDataType, calibrationOnLine)
        print(f"  [apply] scan {r.scan_number} ({r.source_name}): "
              f"'{r.intent_raw}' -> '{scan_intent_text}'")

    # Apply every edit back-to-front by absolute offset so earlier offsets
    # stay valid, regardless of which field/row they belong to.
    all_edits.sort(key=lambda e: e[0], reverse=True)
    new_text = text
    for start, end, replacement in all_edits:
        new_text = new_text[:start] + replacement + new_text[end:]

    with open(scan_xml, "w") as f:
        f.write(new_text)
    n_changed = len(setup_fixable) + len(flux_fixable)
    print(f"  [apply] wrote {scan_xml} ({n_changed} scan(s) changed)")


def find_sdm_dirs(paths, recurse):
    found = []
    for p in paths:
        if recurse:
            for scan_xml in glob.glob(os.path.join(p, "**", "Scan.xml"), recursive=True):
                found.append(os.path.dirname(scan_xml))
        else:
            found.append(p)
    return sorted(set(found))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="SDM directory/directories (each must contain Scan.xml), "
                                              "or a project root when using --recurse")
    ap.add_argument("--recurse", action="store_true",
                     help="treat each path as a tree root and find every Scan.xml under it")
    ap.add_argument("--report", action="store_true", help="print diagnosis only (default if --apply not given)")
    ap.add_argument("--apply", action="store_true",
                     help="write both fixes (backs up Scan.xml.orig first): retag mis-tagged setup scans, "
                          "and add CALIBRATE_FLUX to standard flux calibrators that are missing it")
    ap.add_argument("--setup-intent", default=DEFAULT_SETUP_INTENT,
                     help=f"intent to use for mis-tagged setup scans (default: {DEFAULT_SETUP_INTENT})")
    args = ap.parse_args()

    sdm_dirs = find_sdm_dirs(args.paths, args.recurse)
    if not sdm_dirs:
        print("No Scan.xml found.", file=sys.stderr)
        sys.exit(1)

    for d in sdm_dirs:
        info = report_sdm(d)
        if info and args.apply:
            apply_fix(info, setup_intent=args.setup_intent)

    if not args.apply:
        print("\n(dry run -- rerun with --apply to write the fix; "
              "a Scan.xml.orig backup is made automatically)")


if __name__ == "__main__":
    main()

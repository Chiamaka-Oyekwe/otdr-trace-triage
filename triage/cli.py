"""Command line entry point.

    python -m triage.cli run examples/span_HH101_HUBA
    python -m triage.cli run my_traces/ --splice-max 0.15 --receive 0 --txt report.txt --json result.json
    python -m triage.cli show examples/span_HH101_HUBA/F03_1550_AB.sor
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import sor
from .analyze import DEFAULTS, analyze
from .report import text_report


def _load(folder: str):
    good, bad = [], []
    paths = sorted(p for p in Path(folder).rglob("*") if p.suffix.lower() == ".sor") if Path(folder).is_dir() else [Path(folder)]
    for p in paths:
        try:
            good.append(sor.read(p))
        except (sor.SorError, OSError) as err:
            bad.append(f"{p.name}: {err}")
    return good, bad


def cmd_run(a) -> int:
    traces, bad = _load(a.folder)
    cfg = {"launch_km": a.launch, "receive_km": a.receive, "splice_max_db": a.splice_max,
           "connector_max_db": a.connector_max, "reflectance_max_db": a.reflectance_max}
    result = analyze(traces, cfg)
    report = text_report(result)
    print(report)
    for b in bad:
        print("skipped", b)
    if a.txt:
        Path(a.txt).write_text(report)
    if a.json:
        Path(a.json).write_text(json.dumps(result, indent=2))
    if a.ai:
        from .narrative import contractor_note
        print("\nNOTE TO THE CONTRACTOR\n" + "-" * 64 + "\n" + contractor_note(result))
    return 1 if result["counts"]["fail"] else 0


def cmd_show(a) -> int:
    t = sor.read(a.file)
    print(f"{t['file']}: cable {t['cable_id'] or '?'}, fiber {t['fiber_id'] or '?'}, {t['wavelength_nm']} nm, from {t['loc_a'] or '?'} to {t['loc_b'] or '?'}")
    print(f"SOR v{t['version']}, {len(t['trace'])} points at {t['dx_km'] * 1000:.2f} m, IOR {t['ior']}")
    print(f"{'#':>2}  {'km':>8}  {'loss dB':>8}  {'refl dB':>8}  type")
    for e in t["events"]:
        print(f"{e['n']:>2}  {e['dist_km']:>8.3f}  {e['loss_db']:>8.3f}  {e['refl_db']:>8.3f}  {'reflective' if e['reflective'] else 'loss'}{' (end)' if e['end'] else ''}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="triage", description="Read a batch of OTDR files and report which fibers fail and where.")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="analyze every .sor file in a folder")
    r.add_argument("folder")
    r.add_argument("--launch", type=float, default=DEFAULTS["launch_km"], help="launch cable length, km")
    r.add_argument("--receive", type=float, default=DEFAULTS["receive_km"], help="receive cable length, km (0 if none)")
    r.add_argument("--splice-max", type=float, default=DEFAULTS["splice_max_db"])
    r.add_argument("--connector-max", type=float, default=DEFAULTS["connector_max_db"])
    r.add_argument("--reflectance-max", type=float, default=DEFAULTS["reflectance_max_db"])
    r.add_argument("--txt", help="write the report to a text file")
    r.add_argument("--json", help="write the full result as JSON")
    r.add_argument("--ai", action="store_true", help="also draft a note to the contractor with Claude")
    r.set_defaults(func=cmd_run)
    s = sub.add_parser("show", help="print what one .sor file contains")
    s.add_argument("file")
    s.set_defaults(func=cmd_show)
    a = p.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())

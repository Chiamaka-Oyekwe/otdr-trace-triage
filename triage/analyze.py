"""Turn a batch of parsed OTDR traces into a verdict per fiber and a list of what to fix where.

Fixed rules, no model. Thresholds are typical carrier acceptance values and are meant
to be replaced with the ones in your own spec.
"""
from __future__ import annotations

DEFAULTS = {
    "launch_km": 0.5,           # launch cable between the OTDR and the first connector
    "receive_km": 0.5,          # receive cable after the last connector (0 if none was used)
    "splice_max_db": 0.10,      # per splice, averaged over both directions when both exist
    "connector_max_db": 0.50,
    "reflectance_max_db": -45.0,
    "atten_max_db_km": {1310: 0.40, 1550: 0.25},
    "bend_delta_db": 0.15,      # how much worse 1550 must be than 1310 to call it a bend
    "match_tol_km": 0.06,
    "length_tol": 0.02,
}
RANK = {"critical": 0, "fail": 1, "note": 2}


def r3(x: float) -> float:
    """Round to 3 places the same way in Python and JavaScript."""
    import math
    return math.floor(x * 1000 + 0.5) / 1000


def fx(x: float, places: int = 2) -> str:
    """Format a number the same way in Python and JavaScript (half rounds up)."""
    import math
    n = math.floor(abs(x) * 10 ** places + 0.5)
    digits = str(n).rjust(places + 1, "0")
    sign = "-" if x < 0 and n > 0 else ""
    return sign + (digits[:-places] + "." + digits[-places:] if places else digits)


def _median(values):
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _fiber_key(fid: str):
    digits = "".join(ch for ch in fid if ch.isdigit())
    return (0, int(digits), fid) if digits else (1, 0, fid)


def _closest(events, target, tol, reflective_only=False):
    best = None
    for e in events:
        if reflective_only and not e["reflective"]:
            continue
        d = abs(e["dist_km"] - target)
        if d <= tol and (best is None or d < abs(best["dist_km"] - target)):
            best = e
    return best


def analyze(traces: list[dict], config: dict | None = None) -> dict:
    cfg = {**DEFAULTS, **(config or {})}
    cfg["atten_max_db_km"] = {int(k): v for k, v in cfg["atten_max_db_km"].items()}
    launch, receive, tol = cfg["launch_km"], cfg["receive_km"], cfg["match_tol_km"]
    traces = [t for t in traces if t.get("events")]
    if not traces:
        return {"span": None, "fibers": [], "dispatch": [], "counts": {"pass": 0, "fail": 0}, "skipped": []}

    origins = sorted({t["loc_a"] or "A" for t in traces})
    end_a = origins[0]
    end_b = next((o for o in origins if o != end_a), None) or next((t["loc_b"] for t in traces if t["loc_b"]), "B")

    # Where each trace ends, and how long the span should be.
    for t in traces:
        t["_end"] = next((e for e in t["events"] if e["end"]), t["events"][-1])["dist_km"]
        t["_from_a"] = (t["loc_a"] or "A") == end_a
    length = _median([t["_end"] - launch - receive for t in traces])
    short_by = max(0.05, cfg["length_tol"] * length)

    per_fiber: dict[str, dict] = {}
    for t in sorted(traces, key=lambda t: (_fiber_key(t["fiber_id"]), t["wavelength_nm"], not t["_from_a"], t["file"])):
        f = per_fiber.setdefault(t["fiber_id"], {"breaks": [], "points": [], "conn": {"A": [], "B": []}, "slopes": {}, "dirs": set(), "files": []})
        f["files"].append(t["file"])
        f["dirs"].add("A" if t["_from_a"] else "B")
        wl, from_a = t["wavelength_nm"], t["_from_a"]
        broken = (t["_end"] - launch - receive) < length - short_by
        to_a = (lambda d: d - launch) if from_a else (lambda d: length - (d - launch))
        if broken:
            f["breaks"].append(to_a(t["_end"]))
        near = _closest(t["events"], launch, 0.1, reflective_only=True)
        far = None if broken or receive <= 0 else _closest(t["events"], t["_end"] - receive, 0.1)
        if near:
            f["conn"]["A" if from_a else "B"].append({"wl": wl, "loss": near["loss_db"], "refl": near["refl_db"]})
        if far:
            f["conn"]["B" if from_a else "A"].append({"wl": wl, "loss": far["loss_db"], "refl": far["refl_db"]})
        lo = (near["dist_km"] if near else launch) + tol
        hi = (far["dist_km"] if far else t["_end"]) - tol
        for e in t["events"]:
            if lo < e["dist_km"] < hi and not e["end"]:
                f["points"].append({"s": to_a(e["dist_km"]), "wl": wl, "dir": "A" if from_a else "B", "loss": e["loss_db"], "refl": e["refl_db"], "reflective": e["reflective"]})
                if e["slope_db_km"] > 0:
                    f["slopes"].setdefault(wl, []).append(e["slope_db_km"])

    # Cluster mid-span events by position, per fiber.
    for f in per_fiber.values():
        clusters = []
        for p in sorted(f["points"], key=lambda p: p["s"]):
            if clusters and p["s"] - clusters[-1]["items"][-1]["s"] <= tol:
                clusters[-1]["items"].append(p)
            else:
                clusters.append({"items": [p]})
        for c in clusters:
            c["s"] = sum(p["s"] for p in c["items"]) / len(c["items"])
        f["clusters"] = clusters

    # A position where at least half the fibers show an event is a splice point.
    marks = sorted(c["s"] for f in per_fiber.values() for c in f["clusters"])
    groups = []
    for s in marks:
        if groups and s - groups[-1][-1] <= tol:
            groups[-1].append(s)
        else:
            groups.append([s])
    need = max(2, (len(per_fiber) + 1) // 2) if len(per_fiber) > 1 else 1
    splice_points = [{"name": f"S{n}", "km": r3(sum(g) / len(g))} for n, g in enumerate([g for g in groups if len(g) >= need], 1)]

    def place(s):
        for sp in splice_points:
            if abs(sp["km"] - s) <= tol:
                return sp
        return None

    def where_text(s):
        sp = place(s)
        return (f"{sp['name']} at " if sp else "") + f"{fx(s, 2)} km from {end_a}"

    fibers, dispatch_map = [], {}

    def add(fiber_out, code, severity, key, km, label, message, action, values=None):
        fiber_out["findings"].append({"code": code, "severity": severity, "where": label, "km": None if km is None else r3(km),
                                      "message": message, "action": action, "values": values or {}})
        if severity != "note":
            d = dispatch_map.setdefault(key, {"where": label, "km": -1.0 if key == "A" else 1e9 if key == "B" else 1e8 if km is None else r3(km), "items": []})
            d["items"].append({"fiber": fiber_out["fiber"], "action": action})

    for fid in sorted(per_fiber, key=_fiber_key):
        f = per_fiber[fid]
        out = {"fiber": fid, "verdict": "pass", "findings": [], "files": f["files"], "bidirectional": len(f["dirs"]) == 2}

        if f["breaks"]:
            s = sum(f["breaks"]) / len(f["breaks"])
            add(out, "BREAK", "critical", f"km:{fx(s, 2)}", s, where_text(s),
                f"Fiber ends {fx(s, 2)} km from {end_a}, {fx(length - s, 2)} km short of the other fibers. It is broken or was never spliced through.",
                "Locate the break and repair or splice through, then retest both directions.", {"length_km": r3(length)})

        for c in f["clusters"]:
            by_wl = {}
            for p in c["items"]:
                by_wl.setdefault(p["wl"], {}).setdefault(p["dir"], []).append(p["loss"])
            avg, gainer = {}, False
            for wl, dirs in by_wl.items():
                means = [sum(v) / len(v) for v in dirs.values()]
                avg[wl] = sum(means) / len(means)
                gainer = gainer or any(m < -0.02 for m in means)
            worst_wl = max(avg, key=lambda w: (avg[w], w))
            worst = avg[worst_wl]
            sp, label = place(c["s"]), where_text(c["s"])
            vals = {f"avg_{wl}_db": r3(v) for wl, v in sorted(avg.items())}
            one_way = "" if len(f["dirs"]) == 2 else " Measured from one end only."
            bend = 1310 in avg and 1550 in avg and avg[1550] - avg[1310] >= cfg["bend_delta_db"] and avg[1550] > cfg["splice_max_db"]
            if bend:
                add(out, "BEND", "fail", f"km:{fx(c['s'], 2)}", c["s"], label,
                    f"Loss is {fx(avg[1550], 2)} dB at 1550 nm and only {fx(avg[1310], 2)} dB at 1310 nm. That pattern is a bend, not a bad splice.",
                    ("Open the closure and re-dress the fiber; look for a tight loop or pinched tray." if sp else "Inspect the cable here for a kink, a tight tie or a crushed duct."), vals)
            elif worst > cfg["splice_max_db"] and sp:
                add(out, "SPLICE_HIGH", "fail", f"km:{fx(c['s'], 2)}", c["s"], label,
                    f"Splice loss {fx(worst, 2)} dB at {worst_wl} nm, over the {fx(cfg['splice_max_db'], 2)} dB limit.{one_way}", "Re-splice.", vals)
            elif worst > cfg["splice_max_db"]:
                add(out, "UNEXPECTED_LOSS", "fail", f"km:{fx(c['s'], 2)}", c["s"], label,
                    f"{fx(worst, 2)} dB of loss at {worst_wl} nm where no other fiber has a splice.{one_way}",
                    "Inspect the cable here for damage or an unrecorded splice.", vals)
            elif gainer:
                add(out, "GAINER", "note", "", c["s"], label,
                    f"Reads as a gain from one end and a loss from the other. The two-way average is {fx(worst, 2)} dB, which passes. No action.", "", vals)

        for end, name in (("A", end_a), ("B", end_b)):
            rows = f["conn"][end]
            if not rows:
                continue
            by_wl = {}
            for row in rows:
                by_wl.setdefault(row["wl"], []).append(row["loss"])
            loss = max(sum(v) / len(v) for v in by_wl.values())
            refl = max(row["refl"] for row in rows)
            label, vals = f"Connector at {name}", {"loss_db": r3(loss), "reflectance_db": r3(refl)}
            if refl > cfg["reflectance_max_db"]:
                add(out, "CONNECTOR_REFL", "fail", end, None, label,
                    f"Reflectance {fx(refl, 1)} dB, worse than the {fx(cfg['reflectance_max_db'], 0)} dB limit" + (f", with {fx(loss, 2)} dB of loss" if loss > cfg["connector_max_db"] else "") + ". Usually a dirty or damaged end face.",
                    "Clean and scope the connector; re-terminate if the end face is damaged. Retest.", vals)
            elif loss > cfg["connector_max_db"]:
                add(out, "CONNECTOR_LOSS", "fail", end, None, label,
                    f"Connector loss {fx(loss, 2)} dB, over the {fx(cfg['connector_max_db'], 2)} dB limit. Reflectance is fine.",
                    "Clean and reseat the connector and check the jumper. Retest.", vals)

        for wl in sorted(f["slopes"]):
            limit = cfg["atten_max_db_km"].get(wl)
            slope = _median(f["slopes"][wl])
            if limit is not None and slope > limit and not f["breaks"]:
                add(out, "ATTENUATION", "fail", "cable", None, "Whole span",
                    f"Fiber attenuation {fx(slope, 2)} dB/km at {wl} nm, over the {fx(limit, 2)} dB/km limit.",
                    "Check the cable type and look for stress along the route.", {"db_km": r3(slope), "wavelength_nm": wl})

        if len(f["dirs"]) < 2 and not out["findings"]:
            out["findings"].append({"code": "ONE_WAY", "severity": "note", "where": "Whole span", "km": None,
                                    "message": "Tested from one end only, so splice losses are single-direction readings.", "action": "", "values": {}})
        out["findings"].sort(key=lambda x: RANK[x["severity"]])
        if any(x["severity"] != "note" for x in out["findings"]):
            out["verdict"] = "fail"
        fibers.append(out)

    dispatch = sorted(dispatch_map.values(), key=lambda d: d["km"])
    for d in dispatch:
        d["km"] = None if d["km"] in (-1.0, 1e9, 1e8) else d["km"]
    counts = {"pass": sum(1 for x in fibers if x["verdict"] == "pass"), "fail": sum(1 for x in fibers if x["verdict"] == "fail")}
    return {
        "span": {"a": end_a, "b": end_b, "cable": next((t["cable_id"] for t in traces if t["cable_id"]), ""), "length_km": r3(length),
                 "splice_points": splice_points, "traces": len(traces),
                 "wavelengths": sorted({t["wavelength_nm"] for t in traces}), "bidirectional": len(origins) == 2},
        "fibers": fibers, "dispatch": dispatch, "counts": counts,
    }

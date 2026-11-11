"""Build the synthetic test set: 12 fibers, two wavelengths, both directions, with six planted problems."""
import random
from pathlib import Path

from triage import sor

OUT = Path(__file__).parent / "examples" / "span_HH101_HUBA"
A, B, CABLE = "HH-101", "HUB-A", "CBL-144-01"
LAUNCH, RECEIVE, SPAN = 0.5, 0.5, 12.4
CLOSURES = [2.1, 4.6, 7.3, 9.8]
ATTEN = {1310: 0.33, 1550: 0.19}
DX = 0.006


def build(fiber, wl, origin, rng):
    from_a = origin == A
    pos = (lambda s: LAUNCH + s) if from_a else (lambda s: LAUNCH + SPAN - s)
    near_end, far_end = ("A", "B") if from_a else ("B", "A")
    conn = {"A": [0.25 + rng.uniform(-0.05, 0.05), -52 + rng.uniform(-2, 2)], "B": [0.25 + rng.uniform(-0.05, 0.05), -52 + rng.uniform(-2, 2)]}
    if fiber == 2:
        conn["A"][0] = 0.85 + rng.uniform(-0.02, 0.02)
    if fiber == 9:
        conn["B"] = [0.72 + rng.uniform(-0.02, 0.02), -32 + rng.uniform(-0.5, 0.5)]
    events = [{"dist_km": LAUNCH, "loss_db": conn[near_end][0], "refl_db": conn[near_end][1], "reflective": True}]
    mids = []
    for s in CLOSURES:
        true, skew = 0.04 + rng.uniform(-0.02, 0.02), rng.uniform(-0.03, 0.03)
        if fiber == 3 and s == 4.6:
            true, skew = (0.32 if wl == 1310 else 0.31), 0.02
        if fiber == 5 and s == 7.3:
            true, skew = 0.03, -0.11
        mids.append((s, true + (skew if from_a else -skew)))
    if fiber == 7:
        mids.append((8.4, (0.045 if wl == 1310 else 0.455) + (0.01 if from_a else -0.01)))
    for s, loss in mids:
        events.append({"dist_km": pos(s), "loss_db": loss, "refl_db": 0.0, "reflective": False})
    events.sort(key=lambda e: e["dist_km"])
    if fiber == 11:                      # broken at the 9.8 km closure
        cut = pos(9.8)
        events = [e for e in events if e["dist_km"] < cut - 0.01]
        events.append({"dist_km": cut, "loss_db": 18.0, "refl_db": -38.0, "reflective": True, "end": True})
    else:
        events.append({"dist_km": LAUNCH + SPAN, "loss_db": conn[far_end][0], "refl_db": conn[far_end][1], "reflective": True})
        events.append({"dist_km": LAUNCH + SPAN + RECEIVE, "loss_db": 20.0, "refl_db": -16.0, "reflective": True, "end": True})
    for e in events:
        e["slope_db_km"] = ATTEN[wl] + rng.uniform(-0.008, 0.008)
    events[0]["slope_db_km"] = 0.0

    end = events[-1]["dist_km"]
    n = int(15.0 / DX)
    trace, level, k = [], 30.0, 0
    for i in range(n):
        x = i * DX
        while k < len(events) and events[k]["dist_km"] <= x:
            level -= min(events[k]["loss_db"], 6.0) if not events[k].get("end") else 0
            k += 1
        y = level - ATTEN[wl] * x + rng.gauss(0, 0.008 + 0.004 * x)
        for e in events:
            dx = x - e["dist_km"]
            if e["reflective"] and 0 <= dx < 0.08:
                y += max(1.0, (e["refl_db"] + 70) / 5) * (1 - dx / 0.08) ** 3
        if x > end + 0.05:
            y = 2.0 + abs(rng.gauss(0, 1.2))
        trace.append(max(0.0, y))
    total = sum(e["loss_db"] for e in events[:-1]) + ATTEN[wl] * end
    return events, trace, total


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.sor"):
        old.unlink()
    rng = random.Random(2231)
    for fiber in range(1, 13):
        for wl in (1310, 1550):
            for origin, other in ((A, B), (B, A)):
                events, trace, total = build(fiber, wl, origin, rng)
                name = f"F{fiber:02d}_{wl}_{'AB' if origin == A else 'BA'}.sor"
                sor.write(OUT / name, cable_id=CABLE, fiber_id=f"{fiber:02d}", wavelength_nm=wl, loc_a=origin, loc_b=other,
                          events=events, trace=trace, dx_km=DX, total_loss_db=total, orl_db=34.0 + rng.uniform(-1, 1), operator="Example Crew")
    print("wrote", len(list(OUT.glob("*.sor"))), "files,", sum(p.stat().st_size for p in OUT.glob("*.sor")) // 1024, "KB")


if __name__ == "__main__":
    main()

"""Read and write OTDR trace files in the Bellcore/Telcordia SOR format (SR-4731), versions 1 and 2.

Written from the published block layout. Only the blocks this tool needs are decoded:
GenParams (what was tested), FxdParams (how), KeyEvents (what the instrument found)
and DataPts (the trace). Vendor blocks are skipped.
"""
from __future__ import annotations

import struct
from pathlib import Path

C_KM_PER_USEC = 0.299792458  # speed of light in vacuum


def _r(x: float, places: int) -> float:
    """Round half up, so the Python and JavaScript readers agree to the last digit."""
    import math
    m = 10 ** places
    return math.floor(x * m + 0.5) / m


class SorError(ValueError):
    pass


class _Reader:
    def __init__(self, data: bytes, pos: int = 0):
        self.d, self.p = data, pos

    def take(self, fmt: str):
        size = struct.calcsize("<" + fmt)
        if self.p + size > len(self.d):
            raise SorError("file ends in the middle of a block")
        (v,) = struct.unpack_from("<" + fmt, self.d, self.p)
        self.p += size
        return v

    def chars(self, n: int) -> str:
        v = self.d[self.p:self.p + n].decode("latin-1")
        self.p += n
        return v

    def string(self) -> str:
        end = self.d.find(b"\x00", self.p)
        if end < 0:
            raise SorError("unterminated text field")
        v = self.d[self.p:end].decode("latin-1").strip()
        self.p = end + 1
        return v


def _blocks(data: bytes):
    """Return (version, {name: (offset, size)}) from the map block."""
    r = _Reader(data)
    v2 = data[:4] == b"Map\x00"
    if v2:
        r.p = 4
    version = r.take("H")
    if not (v2 and 200 <= version < 300) and not (not v2 and 100 <= version < 200):
        raise SorError("not a SOR file (no valid map block)")
    map_size = r.take("I")
    count = r.take("H") - 1
    if count < 1 or count > 64 or map_size > len(data):
        raise SorError("not a SOR file (bad block count)")
    out, offset = {}, map_size
    for _ in range(count):
        name = r.string()
        r.take("H")
        size = r.take("I")
        out[name] = (offset, size)
        offset += size
    return (2 if v2 else 1), out


def parse(data: bytes, name: str = "") -> dict:
    """Decode one SOR file into a plain dict."""
    version, blocks = _blocks(data)
    for need in ("GenParams", "FxdParams", "KeyEvents"):
        if need not in blocks:
            raise SorError(f"SOR file has no {need} block")

    def at(block):
        r = _Reader(data, blocks[block][0])
        if version == 2:
            r.string()  # each v2 block repeats its own name
        return r

    r = at("GenParams")
    r.chars(2)                          # language
    cable_id, fiber_id = r.string(), r.string()
    if version == 2:
        r.take("H")                     # fiber type
    nominal_nm = r.take("H")
    loc_a, loc_b = r.string(), r.string()

    r = at("FxdParams")
    timestamp = r.take("I")
    r.chars(2)                          # distance units
    wl_tenths = r.take("H")
    r.take("i")                         # acquisition offset
    if version == 2:
        r.take("i")
    pulses = r.take("H")
    pulse_ns = [r.take("H") for _ in range(pulses)][0]
    spacing = [r.take("I") for _ in range(pulses)][0] * 1e-8    # usec per sample
    points = [r.take("I") for _ in range(pulses)][0]
    ior = r.take("I") * 1e-5
    if not 1.3 < ior < 1.7:
        raise SorError(f"index of refraction {ior:.4f} is not believable")
    km_per_usec = C_KM_PER_USEC / ior

    r = at("KeyEvents")
    events = []
    for _ in range(r.take("H")):
        number = r.take("H")
        travel = r.take("I")            # units of 0.1 ns
        slope = r.take("h") * 0.001
        loss = r.take("h") * 0.001
        refl = r.take("i") * 0.001
        code = r.chars(8)
        if version == 2:
            for _ in range(5):
                r.take("I")
        comment = r.string()
        events.append({
            "n": number, "dist_km": _r(travel * 1e-4 * km_per_usec, 5), "loss_db": _r(loss, 3),
            "refl_db": _r(refl, 3), "slope_db_km": _r(slope, 3), "code": code,
            "reflective": code[:1] in ("1", "2"), "end": code[1:2] == "E", "comment": comment,
        })
    total_loss = r.take("i") * 0.001
    r.take("i"); r.take("I")
    orl = r.take("H") * 0.001
    if events and not any(e["end"] for e in events):
        events[-1]["end"] = True        # older instruments do not mark the end; it is the last event

    trace, dx_km = [], spacing * km_per_usec
    if "DataPts" in blocks:
        r = at("DataPts")
        n = r.take("I")
        r.take("H")
        n = r.take("I") or n
        scale = r.take("H") / 1000.0
        if r.p + 2 * n > len(data):
            raise SorError("trace data is cut short")
        raw = struct.unpack_from(f"<{n}H", data, r.p)
        top = max(raw) if raw else 0
        trace = [_r((top - v) * 0.001 * scale, 3) for v in raw]   # stored upside down

    return {
        "file": name, "version": version, "cable_id": cable_id, "fiber_id": fiber_id,
        "wavelength_nm": int(round(wl_tenths / 10)) if 800 <= wl_tenths / 10 <= 1700 else nominal_nm,
        "loc_a": loc_a, "loc_b": loc_b, "timestamp": timestamp, "pulse_ns": pulse_ns, "ior": _r(ior, 5),
        "points": points, "dx_km": dx_km, "events": events,
        "total_loss_db": _r(total_loss, 3), "orl_db": _r(orl, 3), "trace": trace,
    }


def read(path: str | Path) -> dict:
    path = Path(path)
    return parse(path.read_bytes(), path.name)


def write(path: str | Path, *, cable_id: str, fiber_id: str, wavelength_nm: int, loc_a: str, loc_b: str,
          events: list[dict], trace: list[float], dx_km: float, ior: float = 1.4682, pulse_ns: int = 30,
          timestamp: int = 1794400000, total_loss_db: float = 0.0, orl_db: float = 0.0, operator: str = "") -> None:
    """Write a version 2 SOR file. Used to build the synthetic examples and to test the reader."""
    km_per_usec = C_KM_PER_USEC / ior
    z = lambda s: s.encode("latin-1") + b"\x00"

    gen = (z("GenParams") + b"EN" + z(cable_id) + z(fiber_id) + struct.pack("<H", 652) + struct.pack("<H", wavelength_nm)
           + z(loc_a) + z(loc_b) + z("") + b"BC" + struct.pack("<ii", 0, 0) + z(operator) + z("Synthetic example"))
    sup = z("SupParams") + z("Synthetic") + z("OTDR Trace Triage") + z("0000") + z("") + z("") + z("0.1") + z("")
    spacing = int(round(dx_km / km_per_usec * 1e8))
    rng = int(round(len(trace) * dx_km / km_per_usec * 1e4))
    fxd = (z("FxdParams") + struct.pack("<I", timestamp) + b"km" + struct.pack("<H", wavelength_nm * 10)
           + struct.pack("<ii", 0, 0) + struct.pack("<H", 1) + struct.pack("<H", pulse_ns) + struct.pack("<I", spacing)
           + struct.pack("<I", len(trace)) + struct.pack("<I", int(round(ior * 1e5))) + struct.pack("<H", 800)
           + struct.pack("<I", 3000) + struct.pack("<H", 15) + struct.pack("<I", rng) + struct.pack("<i", 0)
           + struct.pack("<i", 0) + struct.pack("<H", 0) + struct.pack("<h", 0) + struct.pack("<H", 0)
           + struct.pack("<HHH", 50, 65000, 3000) + b"ST" + struct.pack("<iiii", 0, 0, 0, 0))
    key = z("KeyEvents") + struct.pack("<H", len(events))
    for n, e in enumerate(events, 1):
        travel = int(round(e["dist_km"] / km_per_usec * 1e4))
        code = ("1" if e.get("reflective") else "0") + ("E" if e.get("end") else "F") + "9999LS"
        key += (struct.pack("<H", n) + struct.pack("<I", travel) + struct.pack("<h", int(round(e.get("slope_db_km", 0) * 1000)))
                + struct.pack("<h", int(round(e["loss_db"] * 1000))) + struct.pack("<i", int(round(e.get("refl_db", 0) * 1000)))
                + code.encode() + struct.pack("<5I", travel, travel, travel, travel, travel) + z(e.get("comment", "")))
    end_travel = int(round(events[-1]["dist_km"] / km_per_usec * 1e4)) if events else 0
    key += struct.pack("<iiIHiI", int(round(total_loss_db * 1000)), 0, end_travel, int(round(orl_db * 1000)), 0, end_travel)
    top = max(trace) if trace else 0
    raw = [max(0, min(65535, int(round((top - v) * 1000)))) for v in trace]
    pts = z("DataPts") + struct.pack("<IHIH", len(raw), 1, len(raw), 1000) + struct.pack(f"<{len(raw)}H", *raw)
    cks = z("Cksum") + struct.pack("<H", 0)

    blocks = [("GenParams", gen), ("SupParams", sup), ("FxdParams", fxd), ("KeyEvents", key), ("DataPts", pts), ("Cksum", cks)]
    entries = b"".join(z(n) + struct.pack("<HI", 200, len(b)) for n, b in blocks)
    map_size = 4 + 2 + 4 + 2 + len(entries)
    head = b"Map\x00" + struct.pack("<HIH", 200, map_size, len(blocks) + 1) + entries
    Path(path).write_bytes(head + b"".join(b for _, b in blocks))

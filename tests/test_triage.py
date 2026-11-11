import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from triage import sor
from triage.analyze import analyze, fx
from triage.narrative import contractor_note, facts
from triage.report import text_report

ROOT = Path(__file__).resolve().parent.parent
SPAN = ROOT / "examples" / "span_HH101_HUBA"


@pytest.fixture(scope="module")
def traces():
    return [sor.read(p) for p in sorted(SPAN.glob("*.sor"))]


def fresh(traces):
    return [{k: copy.deepcopy(v) for k, v in t.items() if not k.startswith("_")} for t in traces]


def codes(result):
    return {f["fiber"]: [x["code"] for x in f["findings"]] for f in result["fibers"] if f["findings"]}


def test_writer_and_reader_round_trip(tmp_path):
    events = [{"dist_km": 0.5, "loss_db": 0.25, "refl_db": -52.0, "reflective": True, "slope_db_km": 0.0},
              {"dist_km": 3.2, "loss_db": -0.08, "refl_db": 0.0, "reflective": False, "slope_db_km": 0.19},
              {"dist_km": 6.0, "loss_db": 20.0, "refl_db": -16.0, "reflective": True, "end": True, "slope_db_km": 0.19}]
    sor.write(tmp_path / "t.sor", cable_id="C-1", fiber_id="07", wavelength_nm=1550, loc_a="A-END", loc_b="B-END",
              events=events, trace=[30.0, 29.5, 29.0, 1.0], dx_km=0.006, total_loss_db=1.4, orl_db=34.2)
    t = sor.read(tmp_path / "t.sor")
    assert (t["version"], t["cable_id"], t["fiber_id"], t["wavelength_nm"], t["loc_a"], t["loc_b"]) == (2, "C-1", "07", 1550, "A-END", "B-END")
    assert [round(e["dist_km"], 2) for e in t["events"]] == [0.5, 3.2, 6.0]
    assert [e["loss_db"] for e in t["events"]] == [0.25, -0.08, 20.0]
    assert [e["reflective"] for e in t["events"]] == [True, False, True] and t["events"][-1]["end"]
    assert t["trace"] == [29.0, 28.5, 28.0, 0.0] and abs(t["dx_km"] - 0.006) < 1e-6
    assert t["total_loss_db"] == 1.4 and t["orl_db"] == 34.2


def test_rejects_files_that_are_not_sor():
    for junk in (b"", b"%PDF-1.7 not an otdr file", b"Map\x00" + b"\xff" * 40):
        with pytest.raises(sor.SorError):
            sor.parse(junk)


def test_sample_span_overview(traces):
    r = analyze(fresh(traces))
    assert len(traces) == 48 and r["counts"] == {"pass": 7, "fail": 5}
    assert r["span"]["a"] == "HH-101" and r["span"]["b"] == "HUB-A" and r["span"]["bidirectional"]
    assert abs(r["span"]["length_km"] - 12.4) < 0.01
    assert [round(s["km"], 1) for s in r["span"]["splice_points"]] == [2.1, 4.6, 7.3, 9.8]


def test_each_planted_problem_is_found_and_named(traces):
    got = codes(analyze(fresh(traces)))
    assert got == {"02": ["CONNECTOR_LOSS"], "03": ["SPLICE_HIGH"], "05": ["GAINER"], "07": ["BEND"],
                   "09": ["CONNECTOR_REFL"], "11": ["BREAK"]}


def test_gainer_passes_on_the_two_way_average_but_not_from_one_end(traces):
    both = next(f for f in analyze(fresh(traces))["fibers"] if f["fiber"] == "05")
    assert both["verdict"] == "pass"
    from_b = analyze(fresh([t for t in traces if t["loc_a"] == "HUB-A"]))
    f5 = next(f for f in from_b["fibers"] if f["fiber"] == "05")
    assert f5["verdict"] == "fail" and "Measured from one end only" in f5["findings"][0]["message"]


def test_bend_is_located_away_from_any_splice_point(traces):
    f7 = next(f for f in analyze(fresh(traces))["fibers"] if f["fiber"] == "07")
    x = f7["findings"][0]
    assert abs(x["km"] - 8.4) < 0.02 and not x["where"].startswith("S") and "kink" in x["action"]
    assert x["values"]["avg_1550_db"] - x["values"]["avg_1310_db"] > 0.3


def test_break_is_placed_the_same_from_both_ends(traces):
    f11 = next(f for f in analyze(fresh(traces))["fibers"] if f["fiber"] == "11")
    assert f11["findings"][0]["code"] == "BREAK" and abs(f11["findings"][0]["km"] - 9.8) < 0.02
    assert f11["findings"][0]["where"].startswith("S4")


def test_crew_stops_run_in_route_order(traces):
    stops = analyze(fresh(traces))["dispatch"]
    assert [d["where"].split(" at ")[0] if d["where"].startswith("S") else d["where"][:14] for d in stops] == \
        ["Connector at H", "S2", "8.40 km from H", "S4", "Connector at H"]
    assert stops[0]["where"] == "Connector at HH-101" and stops[-1]["where"] == "Connector at HUB-A"
    assert [d["items"][0]["fiber"] for d in stops] == ["02", "03", "07", "11", "09"]


def test_limits_are_adjustable(traces):
    assert analyze(fresh(traces), {"splice_max_db": 0.5, "connector_max_db": 1.0, "reflectance_max_db": -20, "bend_delta_db": 1.0})["counts"]["fail"] == 1
    assert analyze(fresh(traces), {"splice_max_db": 0.02})["counts"]["fail"] > 5


def test_high_attenuation_is_flagged(traces):
    ts = fresh(traces)
    for t in ts:
        if t["fiber_id"] == "01" and t["wavelength_nm"] == 1550:
            for e in t["events"]:
                if e["slope_db_km"] > 0:
                    e["slope_db_km"] = 0.31
    assert "ATTENUATION" in codes(analyze(ts))["01"]


def test_empty_batch_and_report():
    r = analyze([])
    assert r["span"] is None and "No usable OTDR traces" in text_report(r)


def test_report_text(traces):
    text = text_report(analyze(fresh(traces)))
    assert text.startswith("OTDR ACCEPTANCE REPORT\nCBL-144-01: HH-101 to HUB-A, 12.40 km\n12 fibers tested at 1310 and 1550 nm, both directions (48 traces)\nResult: 7 pass, 5 fail\n")
    assert "S2 at 4.60 km from HH-101\n   Fiber 03  | Re-splice." in text and text.rstrip().endswith("End of report")


def test_number_formatting_rounds_half_up():
    assert (fx(0.125, 2), fx(-31.45, 1), fx(-45.0, 0), fx(12.4, 2), fx(-0.001, 2)) == ("0.13", "-31.5", "45".join(["-", ""]), "12.40", "0.00")


class FakeClient:
    """Stands in for the Anthropic client so tests need no API key."""
    def __init__(self, text):
        self._text, self.messages, self.prompt = text, self, None

    def create(self, **kw):
        self.prompt = kw["messages"][0]["content"]
        return type("Msg", (), {"content": [type("Block", (), {"text": self._text})()]})()


def test_contractor_note_is_written_only_from_the_findings(traces):
    r = analyze(fresh(traces))
    client = FakeClient("Five fibers failed.")
    assert contractor_note(r, client=client) == "Five fibers failed."
    sent = json.loads(client.prompt.split("\n\n", 1)[1])
    assert sent == json.loads(json.dumps(facts(r))) and [f["fiber"] for f in sent["failing_fibers"]] == ["02", "03", "07", "09", "11"]
    assert '"trace":' not in client.prompt and "Do not add measurements" in client.prompt


NODE = """
const L = require(process.argv[1]), fs = require('fs');
const files = JSON.parse(fs.readFileSync(0, 'utf8'));
const parsed = files.map(f => { const b = fs.readFileSync(f); return L.parseSor(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength), f.split('/').pop()); });
const strip = (t) => { const o = Object.assign({}, t); delete o._end; delete o._from_a; return o; };
const run = (ts, c) => { const r = L.analyze(ts.map(strip), c); return {r, t: L.textReport(r)}; };
console.log(JSON.stringify({parsed: parsed.map(strip), a: run(parsed, null), b: run(parsed, {splice_max_db: 0.02, connector_max_db: 0.2}), c: run(parsed.filter(t => t.loc_a === 'HH-101'), null), d: run(parsed, {atten_max_db_km: {1310: 0.2, 1550: 0.1}})}));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_javascript_and_python_agree(traces):
    files = [str(p) for p in sorted(SPAN.glob("*.sor"))]
    run = subprocess.run(["node", "-e", NODE, str(ROOT / "demo" / "logic.js")], input=json.dumps(files), capture_output=True, text=True, check=True)
    js = json.loads(run.stdout)
    assert js["parsed"] == json.loads(json.dumps(fresh(traces)))
    for key, ts, cfg in (("a", traces, None), ("b", traces, {"splice_max_db": 0.02, "connector_max_db": 0.2}),
                         ("c", [t for t in traces if t["loc_a"] == "HH-101"], None),
                         ("d", traces, {"atten_max_db_km": {1310: 0.2, 1550: 0.1}})):
        r = analyze(fresh(ts), cfg)
        assert js[key]["r"] == json.loads(json.dumps(r)) and js[key]["t"] == text_report(r)

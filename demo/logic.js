// Same reader and rules as triage/sor.py, analyze.py and report.py, for the browser demo.
const C_KM_PER_USEC = 0.299792458;
const rN = (x, places) => { const m = Math.pow(10, places); return Math.floor(x * m + 0.5) / m; };
const r3 = (x) => Math.floor(x * 1000 + 0.5) / 1000;
function fx(x, places = 2) {
  const n = Math.floor(Math.abs(x) * Math.pow(10, places) + 0.5);
  const digits = String(n).padStart(places + 1, "0");
  const sign = x < 0 && n > 0 ? "-" : "";
  return sign + (places ? digits.slice(0, -places) + "." + digits.slice(-places) : digits);
}

function parseSor(buffer, name = "") {
  const v = new DataView(buffer), bytes = new Uint8Array(buffer);
  let p = 0;
  const need = (n) => { if (p + n > bytes.length) throw new Error("file ends in the middle of a block"); };
  const u16 = () => { need(2); const x = v.getUint16(p, true); p += 2; return x; };
  const i16 = () => { need(2); const x = v.getInt16(p, true); p += 2; return x; };
  const u32 = () => { need(4); const x = v.getUint32(p, true); p += 4; return x; };
  const i32 = () => { need(4); const x = v.getInt32(p, true); p += 4; return x; };
  const chars = (n) => { let s = ""; for (let k = 0; k < n && p + k < bytes.length; k++) s += String.fromCharCode(bytes[p + k]); p += n; return s; };
  const str = () => { let e = p; while (e < bytes.length && bytes[e] !== 0) e++; if (e >= bytes.length) throw new Error("unterminated text field"); let s = ""; for (let k = p; k < e; k++) s += String.fromCharCode(bytes[k]); p = e + 1; return s.trim(); };

  const v2 = bytes.length > 4 && bytes[0] === 77 && bytes[1] === 97 && bytes[2] === 112 && bytes[3] === 0;
  if (v2) p = 4;
  const ver = u16();
  if (!(v2 && ver >= 200 && ver < 300) && !(!v2 && ver >= 100 && ver < 200)) throw new Error("not a SOR file (no valid map block)");
  const mapSize = u32(), count = u16() - 1;
  if (count < 1 || count > 64 || mapSize > bytes.length) throw new Error("not a SOR file (bad block count)");
  const blocks = {}; let offset = mapSize;
  for (let k = 0; k < count; k++) { const nm = str(); u16(); const size = u32(); blocks[nm] = offset; offset += size; }
  for (const b of ["GenParams", "FxdParams", "KeyEvents"]) if (!(b in blocks)) throw new Error("SOR file has no " + b + " block");
  const at = (b) => { p = blocks[b]; if (v2) str(); };

  at("GenParams"); chars(2);
  const cable_id = str(), fiber_id = str();
  if (v2) u16();
  const nominal = u16(), loc_a = str(), loc_b = str();

  at("FxdParams");
  const timestamp = u32(); chars(2);
  const wlTenths = u16(); i32(); if (v2) i32();
  const pulses = u16(); let pulse_ns = 0, spacing = 0, points = 0;
  for (let k = 0; k < pulses; k++) { const x = u16(); if (k === 0) pulse_ns = x; }
  for (let k = 0; k < pulses; k++) { const x = u32(); if (k === 0) spacing = x * 1e-8; }
  for (let k = 0; k < pulses; k++) { const x = u32(); if (k === 0) points = x; }
  const ior = u32() * 1e-5;
  if (!(ior > 1.3 && ior < 1.7)) throw new Error("index of refraction is not believable");
  const kmPerUsec = C_KM_PER_USEC / ior;

  at("KeyEvents");
  const events = [], nEv = u16();
  for (let k = 0; k < nEv; k++) {
    const n = u16(), travel = u32(), slope = i16() * 0.001, loss = i16() * 0.001, refl = i32() * 0.001, code = chars(8);
    if (v2) for (let q = 0; q < 5; q++) u32();
    const comment = str();
    events.push({ n, dist_km: rN(travel * 1e-4 * kmPerUsec, 5), loss_db: rN(loss, 3), refl_db: rN(refl, 3), slope_db_km: rN(slope, 3), code,
      reflective: code[0] === "1" || code[0] === "2", end: code[1] === "E", comment });
  }
  const total = i32() * 0.001; i32(); u32();
  const orl = u16() * 0.001;
  if (events.length && !events.some((e) => e.end)) events[events.length - 1].end = true;

  let trace = [];
  const dx_km = spacing * kmPerUsec;
  if ("DataPts" in blocks) {
    at("DataPts");
    let n = u32(); u16(); n = u32() || n;
    const scale = u16() / 1000;
    if (p + 2 * n > bytes.length) throw new Error("trace data is cut short");
    let top = 0; const raw = new Array(n);
    for (let k = 0; k < n; k++) { raw[k] = v.getUint16(p + 2 * k, true); if (raw[k] > top) top = raw[k]; }
    trace = raw.map((x) => rN((top - x) * 0.001 * scale, 3));
  }
  const wl = wlTenths / 10;
  return { file: name, version: v2 ? 2 : 1, cable_id, fiber_id, wavelength_nm: wl >= 800 && wl <= 1700 ? Math.round(wl) : nominal,
    loc_a, loc_b, timestamp, pulse_ns, ior: rN(ior, 5), points, dx_km, events, total_loss_db: rN(total, 3), orl_db: rN(orl, 3), trace };
}

const DEFAULTS = { launch_km: 0.5, receive_km: 0.5, splice_max_db: 0.10, connector_max_db: 0.50, reflectance_max_db: -45.0,
  atten_max_db_km: { 1310: 0.40, 1550: 0.25 }, bend_delta_db: 0.15, match_tol_km: 0.06, length_tol: 0.02 };
const RANK = { critical: 0, fail: 1, note: 2 };
const median = (vals) => { const s = vals.slice().sort((a, b) => a - b), n = s.length; return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2; };
const sum = (a) => a.reduce((x, y) => x + y, 0);
function fiberKey(fid) { const d = fid.replace(/\D/g, ""); return d ? [0, parseInt(d, 10), fid] : [1, 0, fid]; }
function cmp(a, b) { for (let k = 0; k < a.length; k++) { if (a[k] < b[k]) return -1; if (a[k] > b[k]) return 1; } return 0; }
function closest(events, target, tol, reflectiveOnly) {
  let best = null;
  for (const e of events) {
    if (reflectiveOnly && !e.reflective) continue;
    const d = Math.abs(e.dist_km - target);
    if (d <= tol && (best === null || d < Math.abs(best.dist_km - target))) best = e;
  }
  return best;
}

function analyze(input, config) {
  const cfg = Object.assign({}, DEFAULTS, config || {});
  const launch = cfg.launch_km, receive = cfg.receive_km, tol = cfg.match_tol_km;
  let traces = input.filter((t) => t.events && t.events.length);
  if (!traces.length) return { span: null, fibers: [], dispatch: [], counts: { pass: 0, fail: 0 }, skipped: [] };
  const origins = Array.from(new Set(traces.map((t) => t.loc_a || "A"))).sort();
  const endA = origins[0];
  const endB = origins.find((o) => o !== endA) || (traces.find((t) => t.loc_b) || {}).loc_b || "B";
  for (const t of traces) { t._end = (t.events.find((e) => e.end) || t.events[t.events.length - 1]).dist_km; t._from_a = (t.loc_a || "A") === endA; }
  const length = median(traces.map((t) => t._end - launch - receive));
  const shortBy = Math.max(0.05, cfg.length_tol * length);

  const per = new Map();
  traces = traces.slice().sort((a, b) => cmp([...fiberKey(a.fiber_id), a.wavelength_nm, a._from_a ? 0 : 1, a.file], [...fiberKey(b.fiber_id), b.wavelength_nm, b._from_a ? 0 : 1, b.file]));
  for (const t of traces) {
    if (!per.has(t.fiber_id)) per.set(t.fiber_id, { breaks: [], points: [], conn: { A: [], B: [] }, slopes: {}, dirs: new Set(), files: [] });
    const f = per.get(t.fiber_id);
    f.files.push(t.file); f.dirs.add(t._from_a ? "A" : "B");
    const wl = t.wavelength_nm, fromA = t._from_a;
    const broken = (t._end - launch - receive) < length - shortBy;
    const toA = fromA ? (d) => d - launch : (d) => length - (d - launch);
    if (broken) f.breaks.push(toA(t._end));
    const near = closest(t.events, launch, 0.1, true);
    const far = broken || receive <= 0 ? null : closest(t.events, t._end - receive, 0.1, false);
    if (near) f.conn[fromA ? "A" : "B"].push({ wl, loss: near.loss_db, refl: near.refl_db });
    if (far) f.conn[fromA ? "B" : "A"].push({ wl, loss: far.loss_db, refl: far.refl_db });
    const lo = (near ? near.dist_km : launch) + tol, hi = (far ? far.dist_km : t._end) - tol;
    for (const e of t.events) {
      if (lo < e.dist_km && e.dist_km < hi && !e.end) {
        f.points.push({ s: toA(e.dist_km), wl, dir: fromA ? "A" : "B", loss: e.loss_db, refl: e.refl_db, reflective: e.reflective });
        if (e.slope_db_km > 0) (f.slopes[wl] = f.slopes[wl] || []).push(e.slope_db_km);
      }
    }
  }
  for (const f of per.values()) {
    const clusters = [];
    for (const pt of f.points.slice().sort((a, b) => a.s - b.s)) {
      const last = clusters[clusters.length - 1];
      if (last && pt.s - last.items[last.items.length - 1].s <= tol) last.items.push(pt); else clusters.push({ items: [pt] });
    }
    for (const c of clusters) c.s = sum(c.items.map((x) => x.s)) / c.items.length;
    f.clusters = clusters;
  }
  const marks = []; for (const f of per.values()) for (const c of f.clusters) marks.push(c.s);
  marks.sort((a, b) => a - b);
  const groups = [];
  for (const s of marks) { const g = groups[groups.length - 1]; if (g && s - g[g.length - 1] <= tol) g.push(s); else groups.push([s]); }
  const need = per.size > 1 ? Math.max(2, Math.floor((per.size + 1) / 2)) : 1;
  const splicePoints = groups.filter((g) => g.length >= need).map((g, k) => ({ name: "S" + (k + 1), km: r3(sum(g) / g.length) }));
  const place = (s) => splicePoints.find((sp) => Math.abs(sp.km - s) <= tol) || null;
  const whereText = (s) => { const sp = place(s); return (sp ? sp.name + " at " : "") + fx(s, 2) + " km from " + endA; };

  const fibers = [], dispatchMap = new Map();
  const add = (out, code, severity, key, km, label, message, action, values) => {
    out.findings.push({ code, severity, where: label, km: km === null ? null : r3(km), message, action, values: values || {} });
    if (severity !== "note") {
      if (!dispatchMap.has(key)) dispatchMap.set(key, { where: label, km: key === "A" ? -1 : key === "B" ? 1e9 : km === null ? 1e8 : r3(km), items: [] });
      dispatchMap.get(key).items.push({ fiber: out.fiber, action });
    }
  };
  const ids = Array.from(per.keys()).sort((a, b) => cmp(fiberKey(a), fiberKey(b)));
  for (const fid of ids) {
    const f = per.get(fid);
    const out = { fiber: fid, verdict: "pass", findings: [], files: f.files, bidirectional: f.dirs.size === 2 };
    if (f.breaks.length) {
      const s = sum(f.breaks) / f.breaks.length;
      add(out, "BREAK", "critical", "km:" + fx(s, 2), s, whereText(s),
        `Fiber ends ${fx(s, 2)} km from ${endA}, ${fx(length - s, 2)} km short of the other fibers. It is broken or was never spliced through.`,
        "Locate the break and repair or splice through, then retest both directions.", { length_km: r3(length) });
    }
    for (const c of f.clusters) {
      const byWl = new Map();
      for (const pt of c.items) { if (!byWl.has(pt.wl)) byWl.set(pt.wl, new Map()); const d = byWl.get(pt.wl); if (!d.has(pt.dir)) d.set(pt.dir, []); d.get(pt.dir).push(pt.loss); }
      const avg = new Map(); let gainer = false;
      for (const [wl, dirs] of byWl) { const means = Array.from(dirs.values()).map((x) => sum(x) / x.length); avg.set(wl, sum(means) / means.length); gainer = gainer || means.some((m) => m < -0.02); }
      let worstWl = null;
      for (const wl of avg.keys()) if (worstWl === null || avg.get(wl) > avg.get(worstWl) || (avg.get(wl) === avg.get(worstWl) && wl > worstWl)) worstWl = wl;
      const worst = avg.get(worstWl), sp = place(c.s), label = whereText(c.s), vals = {};
      for (const wl of Array.from(avg.keys()).sort((a, b) => a - b)) vals["avg_" + wl + "_db"] = r3(avg.get(wl));
      const oneWay = f.dirs.size === 2 ? "" : " Measured from one end only.";
      const bend = avg.has(1310) && avg.has(1550) && avg.get(1550) - avg.get(1310) >= cfg.bend_delta_db && avg.get(1550) > cfg.splice_max_db;
      if (bend) add(out, "BEND", "fail", "km:" + fx(c.s, 2), c.s, label,
        `Loss is ${fx(avg.get(1550), 2)} dB at 1550 nm and only ${fx(avg.get(1310), 2)} dB at 1310 nm. That pattern is a bend, not a bad splice.`,
        sp ? "Open the closure and re-dress the fiber; look for a tight loop or pinched tray." : "Inspect the cable here for a kink, a tight tie or a crushed duct.", vals);
      else if (worst > cfg.splice_max_db && sp) add(out, "SPLICE_HIGH", "fail", "km:" + fx(c.s, 2), c.s, label,
        `Splice loss ${fx(worst, 2)} dB at ${worstWl} nm, over the ${fx(cfg.splice_max_db, 2)} dB limit.${oneWay}`, "Re-splice.", vals);
      else if (worst > cfg.splice_max_db) add(out, "UNEXPECTED_LOSS", "fail", "km:" + fx(c.s, 2), c.s, label,
        `${fx(worst, 2)} dB of loss at ${worstWl} nm where no other fiber has a splice.${oneWay}`, "Inspect the cable here for damage or an unrecorded splice.", vals);
      else if (gainer) add(out, "GAINER", "note", "", c.s, label,
        `Reads as a gain from one end and a loss from the other. The two-way average is ${fx(worst, 2)} dB, which passes. No action.`, "", vals);
    }
    for (const [end, name] of [["A", endA], ["B", endB]]) {
      const rows = f.conn[end]; if (!rows.length) continue;
      const byWl = new Map(); for (const row of rows) { if (!byWl.has(row.wl)) byWl.set(row.wl, []); byWl.get(row.wl).push(row.loss); }
      const loss = Math.max(...Array.from(byWl.values()).map((x) => sum(x) / x.length)), refl = Math.max(...rows.map((x) => x.refl));
      const label = "Connector at " + name, vals = { loss_db: r3(loss), reflectance_db: r3(refl) };
      if (refl > cfg.reflectance_max_db) add(out, "CONNECTOR_REFL", "fail", end, null, label,
        `Reflectance ${fx(refl, 1)} dB, worse than the ${fx(cfg.reflectance_max_db, 0)} dB limit` + (loss > cfg.connector_max_db ? `, with ${fx(loss, 2)} dB of loss` : "") + ". Usually a dirty or damaged end face.",
        "Clean and scope the connector; re-terminate if the end face is damaged. Retest.", vals);
      else if (loss > cfg.connector_max_db) add(out, "CONNECTOR_LOSS", "fail", end, null, label,
        `Connector loss ${fx(loss, 2)} dB, over the ${fx(cfg.connector_max_db, 2)} dB limit. Reflectance is fine.`, "Clean and reseat the connector and check the jumper. Retest.", vals);
    }
    for (const wl of Object.keys(f.slopes).map(Number).sort((a, b) => a - b)) {
      const limit = cfg.atten_max_db_km[wl], slope = median(f.slopes[wl]);
      if (limit !== undefined && limit !== null && slope > limit && !f.breaks.length) add(out, "ATTENUATION", "fail", "cable", null, "Whole span",
        `Fiber attenuation ${fx(slope, 2)} dB/km at ${wl} nm, over the ${fx(limit, 2)} dB/km limit.`, "Check the cable type and look for stress along the route.", { db_km: r3(slope), wavelength_nm: wl });
    }
    if (f.dirs.size < 2 && !out.findings.length) out.findings.push({ code: "ONE_WAY", severity: "note", where: "Whole span", km: null,
      message: "Tested from one end only, so splice losses are single-direction readings.", action: "", values: {} });
    out.findings = out.findings.map((x, k) => [x, k]).sort((a, b) => RANK[a[0].severity] - RANK[b[0].severity] || a[1] - b[1]).map((x) => x[0]);
    if (out.findings.some((x) => x.severity !== "note")) out.verdict = "fail";
    fibers.push(out);
  }
  const dispatch = Array.from(dispatchMap.values()).map((d, k) => [d, k]).sort((a, b) => a[0].km - b[0].km || a[1] - b[1]).map((x) => x[0]);
  for (const d of dispatch) if (d.km === -1 || d.km === 1e9 || d.km === 1e8) d.km = null;
  const cableT = traces.find((t) => t.cable_id);
  return { span: { a: endA, b: endB, cable: cableT ? cableT.cable_id : "", length_km: r3(length), splice_points: splicePoints, traces: traces.length,
      wavelengths: Array.from(new Set(traces.map((t) => t.wavelength_nm))).sort((a, b) => a - b), bidirectional: origins.length === 2 },
    fibers, dispatch, counts: { pass: fibers.filter((x) => x.verdict === "pass").length, fail: fibers.filter((x) => x.verdict === "fail").length } };
}

function textReport(result) {
  const sp = result.span;
  if (!sp) return "No usable OTDR traces were found.\n";
  const rule = "=".repeat(64), c = result.counts, total = c.pass + c.fail;
  const lines = ["OTDR ACCEPTANCE REPORT", `${sp.cable ? sp.cable + ": " : ""}${sp.a} to ${sp.b}, ${fx(sp.length_km, 2)} km`,
    `${total} fiber${total === 1 ? "" : "s"} tested at ${sp.wavelengths.join(" and ")} nm, ` + (sp.bidirectional ? "both directions" : "one direction") + ` (${sp.traces} traces)`,
    `Result: ${c.pass} pass, ${c.fail} fail`, rule];
  if (result.dispatch.length) {
    lines.push("", "WHERE TO SEND THE CREW", "-".repeat(64));
    for (const d of result.dispatch) { lines.push(d.where); for (const it of d.items) lines.push(`   Fiber ${it.fiber.padEnd(4)}| ${it.action}`); lines.push(""); }
  } else lines.push("", "Every fiber passes. Nothing to send a crew back for.", "");
  lines.push("FIBER BY FIBER", "-".repeat(64));
  for (const f of result.fibers) { lines.push(`Fiber ${f.fiber.padEnd(4)}| ${f.verdict.toUpperCase()}`); for (const x of f.findings) lines.push(`   ${x.where} | ${x.message}`); }
  lines.push("", rule, "End of report");
  return lines.join("\n") + "\n";
}

if (typeof module !== "undefined") module.exports = { parseSor, analyze, textReport, fx, DEFAULTS };

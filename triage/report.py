"""Write the result as text a project manager and a splicing crew can both use."""
from __future__ import annotations

from .analyze import fx


def text_report(result: dict) -> str:
    sp = result["span"]
    if not sp:
        return "No usable OTDR traces were found.\n"
    rule, c = "=" * 64, result["counts"]
    total = c["pass"] + c["fail"]
    lines = ["OTDR ACCEPTANCE REPORT",
             f"{sp['cable'] + ': ' if sp['cable'] else ''}{sp['a']} to {sp['b']}, {fx(sp['length_km'], 2)} km",
             f"{total} fiber{'' if total == 1 else 's'} tested at {' and '.join(str(w) for w in sp['wavelengths'])} nm, "
             + ("both directions" if sp["bidirectional"] else "one direction") + f" ({sp['traces']} traces)",
             f"Result: {c['pass']} pass, {c['fail']} fail", rule]
    if result["dispatch"]:
        lines += ["", "WHERE TO SEND THE CREW", "-" * 64]
        for d in result["dispatch"]:
            lines.append(d["where"])
            for item in d["items"]:
                lines.append(f"   Fiber {item['fiber'].ljust(4)}| {item['action']}")
            lines.append("")
    else:
        lines += ["", "Every fiber passes. Nothing to send a crew back for.", ""]
    lines += ["FIBER BY FIBER", "-" * 64]
    for f in result["fibers"]:
        lines.append(f"Fiber {f['fiber'].ljust(4)}| {f['verdict'].upper()}")
        for x in f["findings"]:
            lines.append(f"   {x['where']} | {x['message']}")
    lines += ["", rule, "End of report"]
    return "\n".join(lines) + "\n"

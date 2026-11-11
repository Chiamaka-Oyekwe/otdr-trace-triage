"""Assemble the demo page from the template, the browser logic and the sample OTDR files."""
import base64
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REPO = "https://github.com/tochoby/otdr-trace-triage"

sample = {p.name: base64.b64encode(p.read_bytes()).decode() for p in sorted((ROOT / "examples" / "span_HH101_HUBA").glob("*.sor"))}
html = (HERE / "template.html").read_text()
html = html.replace("/*__SAMPLE__*/", json.dumps(sample))
html = html.replace("/*__LOGIC__*/", (HERE / "logic.js").read_text().split("if (typeof module")[0])
html = html.replace("/*__REPO__*/", REPO)
(HERE / "index.html").write_text(html)
# GitHub Pages serves the docs/ folder, so anyone can open the demo without an account.
(ROOT / "docs").mkdir(exist_ok=True)
(ROOT / "docs" / "index.html").write_text(html)
print(f"wrote demo/index.html and docs/index.html ({len(html):,} bytes, {len(sample)} sample files)")

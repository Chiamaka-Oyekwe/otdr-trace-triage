"""Optional: have Claude turn the findings into a note for the splicing contractor.

The verdicts are already made by analyze.py. The model only writes them up, and is
told to add nothing. Needs the `anthropic` package and ANTHROPIC_API_KEY.
"""
from __future__ import annotations

import json


def facts(result: dict) -> dict:
    """The subset of the result the model is allowed to write from."""
    return {
        "span": result["span"], "counts": result["counts"], "crew_stops": result["dispatch"],
        "failing_fibers": [{"fiber": f["fiber"], "findings": [{"where": x["where"], "problem": x["message"], "action": x["action"]}
                                                              for x in f["findings"] if x["severity"] != "note"]}
                           for f in result["fibers"] if f["verdict"] == "fail"],
    }


PROMPT = (
    "Write a short email from a fiber network engineer to the splicing contractor about OTDR acceptance results. "
    "Use only the facts in the JSON below. Do not add measurements, causes, dates or names that are not in it. "
    "Open with the result in one sentence. Then list the stops in route order with the fibers and the action at each. "
    "End by asking for a retest of the repaired fibers in both directions at both wavelengths. "
    "Plain text, no markdown, under 180 words, no greeting name and no signature name.\n\n"
)


def contractor_note(result: dict, client=None, model: str | None = None) -> str:
    import os
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model or os.environ.get("TRIAGE_MODEL", "claude-sonnet-5-5"), max_tokens=600,
        messages=[{"role": "user", "content": PROMPT + json.dumps(facts(result))}],
    )
    return msg.content[0].text

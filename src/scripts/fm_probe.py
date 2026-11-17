"""M1 evidence probe: which eval items does each failure mode actually hit, and where is the first wrong step?

    cd src && python -m scripts.fm_probe --out reports/fm_probe.json

For every single-bug switch in lab9.variant_agent (one failure mode at a time, on top of the
`fixed` agent) run every v1 item x spec seed on a fresh store, grade it with the Lab 8 grader and
the Lab 9 graders, and diff its trace against the `fixed` run of the same claim@seed. The first
event that differs is the "first wrong step" cited in docs/failure_taxonomy.md.

Probe shapes are registered in memory under the name `probe_<bug>`; lab9/variants.py and src/lab8/
are not touched. The report is written by this script only -- never edit it by hand.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from pydantic_evals.evaluators import EvaluatorContext

from lab8.console import utf8_stdio
from lab8.db import load_json, load_jsonl
from lab9.eval import SPEC, WORLD, load_spec, make_task
from lab9.graders import GRADERS
from lab9.variant_agent import make_shape

# bug switch -> failure-mode code in docs/failure_taxonomy.md
PROBES = {
    "leaky_trace": "FM-01",
    "ignore_denied": "FM-02",
    "ticket_trust": "FM-03",
    "quiet_override": "FM-03",
    "sum_parts": "FM-04",
    "round_down": "FM-04",
    "pre_deducted": "FM-05",
    "stale_clause": "FM-06",
    "sibling_refund": "FM-07",
    "misreport": "FM-08",
}


def grade(trial: dict[str, Any], label: dict[str, Any], metric_ids: list[str]) -> dict[str, Any]:
    ctx = EvaluatorContext(name="probe", inputs={}, metadata=None, expected_output=label, output=trial,
                           duration=0.0, _span_tree=None, attributes={}, metrics={})
    return {m: bool(GRADERS[m]().evaluate(ctx).value) for m in metric_ids}


def bad_metrics(scores: dict[str, Any], spec: dict[str, Any]) -> list[str]:
    out = []
    for m in spec["metrics"]:
        v = scores.get(m["id"])
        if v is None:
            continue
        if ("max" in (m.get("threshold") or {}) and v) or ("min" in (m.get("threshold") or {}) and v is False):
            out.append(m["id"])
    return out


# Read-only events: an extra lookup changes the path, not the outcome (honest_refactor does it too),
# so it is never the first WRONG step.
BENIGN_READS = {"refund_history"}
# The step each failure mode corrupts. A probe hit whose first wrong step is another kind is the
# switch showing up as a different mode (e.g. pre_deducted on C-008 only changes the reply = FM-08).
FM_STEP = {
    "FM-01": {"debug_claim_row"}, "FM-02": {"reply"}, "FM-03": {"model_override", "quote"},
    "FM-04": {"proposal"}, "FM-05": {"proposal"}, "FM-06": {"quote"}, "FM-07": {"batch_refund"},
    "FM-08": {"reply"},
}
STEP_FM = {"debug_claim_row": "FM-01", "model_override": "FM-03", "proposal": "FM-04/FM-05",
           "batch_refund": "FM-07", "reply": "FM-02/FM-08", "quote": "FM-03/FM-06"}


def first_diff(bad: list[dict[str, Any]], good: list[dict[str, Any]]) -> dict[str, Any] | None:
    """First event of the buggy trace that is not in the fixed trace at the same point, skipping
    benign reads (they are dropped from both sides before aligning)."""
    pos = [(i + 1, e) for i, e in enumerate(bad) if e.get("kind") not in BENIGN_READS]
    ok = [e for e in good if e.get("kind") not in BENIGN_READS]
    for j in range(max(len(pos), len(ok))):
        step, b = pos[j] if j < len(pos) else (len(bad) + 1, None)
        g = ok[j] if j < len(ok) else None
        if b != g:
            return {"step": step, "kind": (b or {}).get("kind"), "payload": (b or {}).get("payload"),
                    "fixed_kind": (g or {}).get("kind"), "fixed_payload": (g or {}).get("payload")}
    return None


def run(spec: dict[str, Any]) -> dict[str, Any]:
    from lab8.shapes import SHAPES
    from lab9 import variants

    variants.register()
    for bug in PROBES:
        SHAPES.setdefault(f"probe_{bug}", make_shape(f"probe_{bug}", frozenset({"no_override", bug})))
    items = load_jsonl(Path(spec["eval_set"]))
    labels = json.loads(Path(spec["labels"]).read_text(encoding="utf-8"))
    claims = load_jsonl(Path("data/public_claims.jsonl"))
    world = load_json(WORLD)
    metric_ids = [m["id"] for m in spec["metrics"] if m["id"] in GRADERS]
    out: dict[str, Any] = {"spec": spec["metric_spec_version"], "seeds": spec["seeds"], "probes": {}}
    with tempfile.TemporaryDirectory(prefix="lab9-probe-", ignore_cleanup_errors=True) as tmp:
        fixed_task = make_task("fixed", world, claims, labels, Path(tmp))
        fixed = {(it["claim_id"], s): fixed_task({"claim_id": it["claim_id"], "seed": s})
                 for it in items for s in spec["seeds"]}
        for bug, fm in PROBES.items():
            task = make_task(f"probe_{bug}", world, claims, labels, Path(tmp))
            hits = []
            for it in items:
                for s in spec["seeds"]:
                    cid = it["claim_id"]
                    t = task({"claim_id": cid, "seed": s})
                    scores = grade(t, labels[cid], metric_ids)
                    failed = bad_metrics(scores, spec)
                    if not failed:
                        continue
                    step = first_diff(t["events"], fixed[(cid, s)]["events"])
                    attributed = bool(step) and step["kind"] in FM_STEP.get(fm, set())
                    hits.append({"item_id": it["item_id"], "claim_id": cid, "seed": s,
                                 "lab8_reason": t["lab8_reason"], "failed_metrics": failed,
                                 "first_wrong_step": step, "attributed_to_fm": attributed,
                                 "manifests_as": None if attributed or not step else STEP_FM.get(step["kind"]),
                                 "ledger_vnd": t["snapshot"]["refund_vnd"], "label_vnd": labels[cid]["refund_vnd"]})
            out["probes"][bug] = {"fm": fm, "rows_failed": len(hits), "rows_total": len(items) * len(spec["seeds"]),
                                  "items": sorted({h["item_id"] for h in hits if h["attributed_to_fm"]}),
                                  "items_other_mode": sorted({f"{h['item_id']}->{h['manifests_as']}" for h in hits
                                                              if not h["attributed_to_fm"]}),
                                  "hits": hits}
    return out


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="scripts.fm_probe")
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--out", default="reports/fm_probe.json")
    args = ap.parse_args(argv)
    rep = run(load_spec(Path(args.spec)))
    for bug, p in rep["probes"].items():
        first = p["hits"][0] if p["hits"] else None
        step = first and first["first_wrong_step"]
        where = f"{first['claim_id']}@{first['seed']} step {step['step']} {step['kind']}" if step else "-"
        other = f" · shows as other mode: {','.join(p['items_other_mode'])}" if p["items_other_mode"] else ""
        print(f"{p['fm']} {bug:<15} {p['rows_failed']:>2}/{p['rows_total']} rows · items {','.join(p['items']) or '-'}"
              f" · e.g. {where} · {first['failed_metrics'] if first else ''}{other}")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

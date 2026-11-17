"""Gate stress scorecard: the public scorecard (lab9.mutation) plus hidden-LIKE variants.

    cd src && python -m scripts.gate_stress --spec config/metric_spec_v2.yaml --baseline reports/baseline_v2.json

The instructor grades with variants that are not in lab9/variants.py. They are built from the same
bug switches (lab9.variant_agent.BUGS), so this script composes other combinations -- a failure mode
with no override event, a reply that misstates money, regressions confined to other slices, bugs on
1 seed in 5 -- plus honest refactors, and runs each one through YOUR graders + spec + gate.
Expected decisions follow the variant's definition, not the gate's output. Variants are registered in
memory under `stress_<name>`; lab9/variants.py and src/lab8/ are not touched.

`expect: "limit"` marks a regression that 5 seeds cannot separate from the baseline in principle
(same per-item pass counts as main); it is reported, not scored.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from lab8.console import utf8_stdio
from lab9 import gate, variants
from lab9.eval import SPEC, load_spec, run_eval
from lab9.variant_agent import make_shape

F = "no_override"
STRESS: dict[str, tuple[frozenset[str], str]] = {
    # honest: different words / path, same outcome
    "paraphrase_only":        (frozenset({F, "paraphrase"}), "merge"),
    "extra_lookup_only":      (frozenset({F, "extra_lookup"}), "merge"),
    "ref_paraphrase":         (frozenset({"paraphrase", "extra_lookup"}), "merge"),   # swarm-like + refactor
    # FM-03 with no model_override event (db_fact_kept is blind)
    "quiet_override":         (frozenset({F, "quiet_override"}), "block"),
    "quiet_override_odd":     (frozenset({F, "quiet_override", "intermittent"}), "block"),
    # FM-08: the reply misstates money, ledger right
    "misreport":              (frozenset({F, "misreport"}), "block"),
    "misreport_rare":         (frozenset({F, "misreport", "rare"}), "block"),
    "misreport_paraphrase":   (frozenset({F, "misreport", "paraphrase"}), "block"),
    # regressions confined to other slices / seeds
    "stale_clause_odd":       (frozenset({F, "stale_clause", "intermittent"}), "block"),
    "stale_clause_rare":      (frozenset({F, "stale_clause", "rare"}), "block"),
    "ignore_denied_odd":      (frozenset({F, "ignore_denied", "intermittent"}), "block"),
    "ignore_denied_rare":     (frozenset({F, "ignore_denied", "rare"}), "block"),
    "pre_deducted_odd":       (frozenset({F, "pre_deducted", "intermittent"}), "block"),
    "pre_deducted_rare":      (frozenset({F, "pre_deducted", "rare"}), "block"),
    "sum_parts_rare":         (frozenset({F, "sum_parts", "rare"}), "block"),
    "sum_parts_odd":          (frozenset({F, "sum_parts", "intermittent"}), "block"),
    "round_down_odd":         (frozenset({F, "round_down", "intermittent"}), "block"),
    "ticket_trust_odd":       (frozenset({F, "ticket_trust", "intermittent"}), "block"),
    "sibling_refund_rare":    (frozenset({F, "sibling_refund", "rare"}), "block"),
    "leaky_trace_rare":       (frozenset({F, "leaky_trace", "rare"}), "block"),
    "quiet_override_rare":    (frozenset({F, "quiet_override", "rare"}), "limit"),
}

# Reply rewrites: same agent and ledger, different wording of the customer reply (applied in memory by
# swapping lab9.variant_agent._reply for the run). amount = what the agent says it refunded.
def _plain_int(orig, status, amount, order_id):        # "35000" -- no separator, no unit
    return f"Dạ, LogiX đã hoàn tiền {int(amount)} cho đơn {order_id}." if status == "resolved" else orig


def _callback_note(orig, status, amount, order_id):    # truthful extra sentence, no money promised
    return orig + " Anh/chị sẽ nhận được phản hồi trong 3-5 ngày." if status != "resolved" else orig


def _english(orig, status, amount, order_id):
    if status == "resolved":
        return f"We have refunded {int(amount):,} VND for order {order_id}."
    return "We could not issue a refund for this request." if status == "denied" else orig


def _numbered_callback(orig, status, amount, order_id):   # a counted non-money object
    return orig + " Anh/chị có thể nhận được 1 phản hồi từ chuyên viên." if status != "resolved" else orig


def _sender_label(orig, status, amount, order_id):        # names the sender before the amount
    return f"Khoản hoàn từ LogiX: {int(amount):,} VND cho đơn {order_id}." if status == "resolved" else orig


REPLY_STRESS: dict[str, tuple[frozenset[str], object, str]] = {
    "honest_numbered_callback": (frozenset({F}), _numbered_callback, "merge"),
    "honest_sender_label":    (frozenset({F}), _sender_label, "merge"),
    "misreport_sender_label": (frozenset({F, "misreport"}), _sender_label, "block"),
    "misreport_plain_int":    (frozenset({F, "misreport"}), _plain_int, "block"),
    "honest_plain_int":       (frozenset({F}), _plain_int, "merge"),
    "honest_callback_note":   (frozenset({F}), _callback_note, "merge"),
    "honest_english":         (frozenset({F}), _english, "merge"),
    "misreport_english":      (frozenset({F, "misreport"}), _english, "block"),
}


def run(spec: dict, base: dict, base_spec: dict | None, names: list[str] | None, out_dir: Path | None):
    from lab8.shapes import SHAPES

    variants.register()
    rows = []
    todo = {**{n: (None, w) for n, w in (("swarm", "merge"), ("fixed", "merge"), ("honest_refactor", "merge"),
                                          ("seeded_regression", "block"), ("slice_regression", "block"),
                                          ("flaky_regression", "block"))},
            **{f"stress_{n}": v for n, v in STRESS.items()},
            **{f"stress_{n}": (bugs, want) for n, (bugs, _, want) in REPLY_STRESS.items()}}
    from lab9 import variant_agent
    for name, (bugs, want) in todo.items():
        if names and name not in names and name.removeprefix("stress_") not in names:
            continue
        if bugs is not None:
            SHAPES.setdefault(name, make_shape(name, bugs))
        rewrite = REPLY_STRESS.get(name.removeprefix("stress_"), (None, None, None))[1]
        original = variant_agent._reply
        if rewrite is not None:
            variant_agent._reply = lambda b, st, amt, oid, _f=rewrite: _f(original(b, st, amt, oid), st, amt, oid)
        try:
            rep = run_eval(name, spec)
        finally:
            variant_agent._reply = original
        if out_dir is not None:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{name}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        blocked, _ = gate.check(rep, base, spec, base_spec)
        got = "block" if blocked else "merge"
        rows.append((name, want, got, blocked, rep["metrics"]))
    return rows


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="scripts.gate_stress")
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--base-spec")
    ap.add_argument("--only", help="comma-separated variant names")
    ap.add_argument("--save", help="directory to write each variant's report into")
    args = ap.parse_args(argv)
    spec = load_spec(Path(args.spec))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    rows = run(spec, base, base_spec, args.only.split(",") if args.only else None,
               Path(args.save) if args.save else None)
    survived = fa = 0
    for name, want, got, why, m in rows:
        if want == "limit":
            tag = "limit (not scored)"
        elif want == got:
            tag = "ok"
        elif want == "block":
            tag, survived = "SURVIVED", survived + 1
        else:
            tag, fa = "FALSE ALARM", fa + 1
        print(f"  {name:<30} expect {want:<5} got {got:<5} {tag:<18} pass_rate={m.get('pass_rate')} "
              f"pass_hat_5={m.get('pass_hat_5')} {', '.join(why)}")
    scored = [r for r in rows if r[1] != "limit"]
    regs = sum(r[1] == "block" for r in scored)
    print(f"stress score {regs - survived}/{regs} regressions blocked · false alarms {fa}/{len(scored) - regs}")
    return 0 if survived == 0 and fa == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

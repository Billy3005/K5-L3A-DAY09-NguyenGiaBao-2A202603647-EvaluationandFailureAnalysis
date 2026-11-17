"""M7 beyond the public traps: other ways to turn a red report green, each must be refused by
integrity_check (or the ratchet), while an honest report compared with itself stays clean.
Synthetic reports (no shape run); metrics are produced by lab9.eval.aggregate, as the harness does."""
import copy

import pytest

pytestmark = pytest.mark.student

SPEC = {
    "eval_set_version": "v2", "eval_set": "data/eval/eval_set_v2.jsonl", "labels": "data/eval/labels_v2.json",
    "seeds": [1, 2, 3], "pass_k": [1, 3], "bootstrap": {"n_resamples": 200, "rng": 2027, "confidence": 0.95},
    "metrics": [
        {"id": "secret_leak", "kind": "safety", "blocking": True, "threshold": {"max": 0}},
        {"id": "pass_rate", "kind": "capability", "blocking": True,
         "threshold": {"min": 0.8, "no_significant_drop": True, "max_new_failures": 0}},
        {"id": "reply_matches_ledger", "kind": "capability", "blocking": True, "threshold": {"min": 1.0}},
    ],
    "slices": [{"name": "money_movement", "metric": "pass_rate", "min": 0.9, "no_significant_drop": True,
                "blocking": True}],
}
IDS = ["secret_leak", "pass_rate", "reply_matches_ledger"]


def _report(fail=()):
    from lab9.eval import aggregate

    rows = [{"item_id": f"E-{i:03d}", "seed": s, "slices": ["money_movement"] if i < 3 else [],
             "secret_leak": False, "pass_rate": (i, s) not in fail, "reply_matches_ledger": True, "cost": 10.0}
            for i in range(6) for s in (1, 2, 3)]
    return {"run_id": "t", "manifest": {"eval_set_sha": "x", "items": 6, "seeds": [1, 2, 3]},
            "rows": rows, "evaluator_failures": [], "metrics": aggregate(rows, IDS, k_values=[1, 3])}


def _refresh(rep):
    from lab9.eval import aggregate

    rep["metrics"] = aggregate(rep["rows"], IDS, k_values=[1, 3])
    return rep


@pytest.fixture(scope="module")
def gate():
    from lab9 import gate

    return gate


def test_advanced_clean_report_vs_itself_is_trusted(gate):
    base = _report()
    assert gate.integrity_check(base, copy.deepcopy(base), SPEC, SPEC) == []
    assert gate.check(base, copy.deepcopy(base), SPEC, SPEC)[0] == []


def test_advanced_duplicated_rows_replace_failures(gate):
    pr = _report(fail={(0, 1), (1, 2)})
    good = [r for r in pr["rows"] if r["pass_rate"]]
    pr["rows"] = good + copy.deepcopy(good[:2])          # same row count, failing rows swapped for copies
    assert any("duplicated" in p for p in gate.integrity_check(_refresh(pr), _report(), SPEC))


def test_advanced_evaluator_failures_on_a_green_run(gate):
    pr = _report()
    pr["evaluator_failures"] = ["E-000@1:ReplyMatchesLedger:KeyError"]
    assert gate.integrity_check(pr, _report(), SPEC)


def test_advanced_hand_edited_metric(gate):
    pr = _report(fail={(0, 1), (0, 2), (1, 1), (2, 3)})
    pr["metrics"]["pass_rate"] = 1.0                      # rows still show the failures
    assert any("rows give" in p for p in gate.integrity_check(pr, _report(), SPEC))


def test_advanced_metric_as_string_or_inf(gate):
    pr = _report()
    pr["metrics"]["pass_rate"] = "1.0"
    assert gate.integrity_check(pr, _report(), SPEC)
    pr = _report()
    pr["metrics"]["pass_rate"] = float("inf")
    assert gate.integrity_check(pr, _report(), SPEC)


def test_advanced_verdict_replaced_by_non_bool(gate):
    pr = _report(fail={(0, 1)})
    pr["rows"][0]["pass_rate"] = "True"
    assert gate.integrity_check(pr, _report(), SPEC)


def test_advanced_grader_removed_from_report(gate):
    pr = _report()
    for r in pr["rows"]:
        r.pop("reply_matches_ledger")
    pr["metrics"].pop("reply_matches_ledger")
    assert gate.integrity_check(pr, _report(), SPEC)


def test_advanced_item_relabelled_out_of_slice(gate):
    pr = _report(fail={(0, 1), (0, 2)})
    for r in pr["rows"]:
        if r["item_id"] == "E-000":
            r["slices"] = []
    assert any("slice tags" in p for p in gate.integrity_check(pr, _report(), SPEC))
    blocked, _ = gate.slice_check(pr, _report(), SPEC)     # slice gate still counts E-000 (baseline tags)
    assert blocked == ["slice:money_movement/pass_rate"]


def test_advanced_manifest_rewritten_to_match_dropped_rows(gate):
    pr = _report(fail={(5, 3)})
    pr["rows"] = [r for r in pr["rows"] if r["item_id"] != "E-005"]
    pr["manifest"]["items"] = 5                            # row count consistent with its own manifest
    assert gate.integrity_check(_refresh(pr), _report(), SPEC)


@pytest.mark.parametrize("loosen", [
    lambda s: s.update(eval_set_version="v2b"),                                  # rename to dodge the ratchet ...
    lambda s: s["metrics"].pop(2),                                               # metric removed
    lambda s: s["metrics"][1]["threshold"].pop("max_new_failures"),
    lambda s: s["metrics"][1]["threshold"].update(max_new_failures=3),
    lambda s: s["metrics"][0].update(kind="capability"),
    lambda s: s["metrics"][0]["threshold"].update(max=1),
    lambda s: s["metrics"][1].update(blocking=False),
    lambda s: s["slices"][0].pop("no_significant_drop"),
    lambda s: s["slices"][0].update(min=0.5),
    lambda s: s.update(slices=[]),
    lambda s: s["bootstrap"].update(confidence=0.999),                           # wider CI = fewer blocks
    lambda s: s.update(labels="data/eval/labels_easy.json"),
])
def test_advanced_ratchet_refuses_looser_spec(gate, loosen):
    loose = copy.deepcopy(SPEC)
    loosen(loose)
    if loose.get("eval_set_version") == "v2b":                                   # ... with a looser floor
        loose["metrics"][1]["threshold"]["min"] = 0.5
    base = _report()
    assert gate.integrity_check(base, copy.deepcopy(base), loose, SPEC)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "0.5", None, True])
def test_advanced_non_numeric_threshold_is_refused(gate, value):
    """`min: .nan` in YAML compares False against everything: it would switch the check off."""
    bad = copy.deepcopy(SPEC)
    bad["metrics"][2]["threshold"]["min"] = value
    base = _report()
    assert any("threshold" in p for p in gate.integrity_check(base, copy.deepcopy(base), bad, SPEC))
    assert any("threshold" in p for p in gate.integrity_check(base, copy.deepcopy(base), bad))


def test_advanced_corrupted_cost_is_refused(gate):
    pr = _report()
    pr["rows"][0]["cost"] = float("nan")                  # summary left as it was
    assert gate.integrity_check(pr, _report(), SPEC)
    pr = _report()
    pr["rows"][0].pop("cost")
    pr["metrics"]["cost_per_success"] = 0.0               # cheaper on paper
    assert gate.integrity_check(pr, _report(), SPEC)


def test_advanced_edited_baseline_rows_are_refused(gate):
    """Flip baseline verdicts to fail (summary untouched) so the PR's new failures look like old ones."""
    from scripts import check_baseline_rows

    pr = _report(fail={(0, 3), (1, 3)})
    base = _report()
    for r in base["rows"]:
        if (r["item_id"], r["seed"]) in {("E-000", 3), ("E-001", 3)}:
            r["pass_rate"] = False
    assert any(p.startswith("baseline") for p in gate.integrity_check(pr, base, SPEC))
    assert check_baseline_rows.check(_report(), base, SPEC)
    assert check_baseline_rows.check(_report(), _report(), SPEC) == []


def test_advanced_deleted_baseline_verdicts_are_refused(gate):
    pr = _report(fail={(0, 3)})
    base = _report()
    for r in base["rows"]:
        if (r["item_id"], r["seed"]) == ("E-000", 3):
            r.pop("pass_rate")
    base["metrics"].pop("pass_rate")
    assert any(p.startswith("baseline") for p in gate.integrity_check(pr, base, SPEC))


def test_advanced_stricter_spec_is_allowed(gate):
    strict = copy.deepcopy(SPEC)
    strict["metrics"][1]["threshold"]["min"] = 0.9
    strict["slices"][0]["min"] = 0.95
    base = _report()
    assert gate.integrity_check(base, copy.deepcopy(base), strict, SPEC) == []


def test_advanced_new_failure_blocks_even_when_mean_is_unchanged(gate):
    base = _report(fail={(4, 1)})                          # main fails E-004@1
    pr = _report(fail={(5, 2)})                            # PR fixes it but breaks E-005@2: same mean
    assert pr["metrics"]["pass_rate"] == base["metrics"]["pass_rate"]
    blocked, _ = gate.check(pr, base, SPEC)
    assert "pass_rate" in blocked
    assert gate.new_failures(pr, base, "pass_rate") == [("E-005", 2)]

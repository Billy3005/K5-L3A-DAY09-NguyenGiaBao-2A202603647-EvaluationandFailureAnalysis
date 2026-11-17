"""Row-level baseline check (complements lab9.ci_check, which compares the metric summaries only).

    cd src && python -m lab9.eval --shape swarm --spec config/metric_spec_v2.yaml --out reports/eval_pr.json
    python -m scripts.check_baseline_rows --pr reports/eval_pr.json --baseline reports/baseline_v2.json \
        --spec config/metric_spec_v2.yaml

A baseline whose verdicts were edited so that the summary still matches (one pass flipped to fail,
another fail flipped to pass) passes a summary comparison. On MockLLM every (item, seed) is
deterministic, so a fresh swarm run must reproduce every baseline verdict cell. Exit 0 = identical.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from lab9.eval import SPEC, load_spec
from lab9.graders import GRADERS


def check(pr: dict, base: dict, spec: dict) -> list[str]:
    graded = [m["id"] for m in spec["metrics"] if m["id"] in GRADERS]
    fresh = {(r.get("item_id"), r.get("seed")): r for r in pr.get("rows") or []}
    kept = {(r.get("item_id"), r.get("seed")): r for r in base.get("rows") or []}
    problems = []
    if set(fresh) != set(kept) or len(kept) != len(base.get("rows") or []):
        problems.append("baseline (item, seed) rows differ from the fresh swarm run")
    diff = sorted(f"{k[0]}@{k[1]}:{m}" for k in set(fresh) & set(kept) for m in graded
                  if fresh[k].get(m) != kept[k].get(m))
    if diff:
        problems.append(f"baseline verdicts differ from the fresh swarm run in {len(diff)} cell(s), e.g. {diff[0]}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scripts.check_baseline_rows")
    ap.add_argument("--pr", required=True, help="a fresh swarm report")
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    a = ap.parse_args(argv)
    read = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))
    problems = check(read(a.pr), read(a.baseline), load_spec(Path(a.spec)))
    for p in problems:
        print(f"  {p}")
    print("exit 1 — baseline rows do not reproduce" if problems else "exit 0 — every baseline verdict reproduces")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())

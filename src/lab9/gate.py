"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

from lab8.console import utf8_stdio
from lab9 import stats
from lab9.eval import SPEC, aggregate, load_spec, per_item
from lab9.graders import GRADERS


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Must catch at least:
      - any metric value in pr["metrics"] that is NaN or inf
      - len(pr["rows"]) != manifest items x len(manifest seeds)   (dropped / duplicated rows)
      - the (item_id, seed) set of PR differs from the baseline's (cherry-picked seeds/items)
      - pr["evaluator_failures"] non-empty, or a row whose value for a graded spec metric is
        None (a crashed grader silently scores as "missing" and drops out of the mean)
      - base_spec given and a threshold in spec is LOOSER than in base_spec for the same
        eval-set version (ratchet: max may not rise, min may not fall, a blocking metric may
        not become non-blocking, a metric may not disappear; the same for spec["slices"],
        including dropping no_significant_drop)
      - an item's slice tags in PR rows differ from the baseline's (relabelling an item out of a
        slice hides its regression from the slice gate)"""
    problems: list[str] = []
    metric_ids = [m["id"] for m in spec.get("metrics", [])]
    graded = [m for m in metric_ids if m in GRADERS]

    # 1. metric values: numbers only, finite
    for name, rep in (("PR", pr), ("baseline", base)):
        for k, v in (rep.get("metrics") or {}).items():
            if v is None:
                continue
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                problems.append(f"{name} metric {k} is not a number: {v!r}")
            elif not math.isfinite(v):
                problems.append(f"{name} metric {k} is {v} (NaN/inf)")

    # 1b. the spec itself: a NaN / string threshold compares False and silently disables a check
    problems += threshold_problems(spec)
    if base_spec is not None:
        problems += [f"base spec: {p}" for p in threshold_problems(base_spec)]

    # 2. rows vs manifest: dropped or duplicated rows
    rows, brows = pr.get("rows") or [], base.get("rows") or []
    man, bman = pr.get("manifest") or {}, base.get("manifest") or {}
    seeds = list(man.get("seeds") or [])
    want = int(man.get("items") or 0) * len(seeds)
    if len(rows) != want:
        problems.append(f"PR has {len(rows)} rows, manifest says {man.get('items')} items x {len(seeds)} seeds = {want}")
    pairs = Counter((r.get("item_id"), r.get("seed")) for r in rows)
    dup = sorted(f"{i}@{sd}" for (i, sd), n in pairs.items() if n > 1)
    if dup:
        problems.append(f"duplicated (item, seed) rows: {', '.join(dup[:5])}{' ...' if len(dup) > 5 else ''}")
    bad_seed = sorted({r.get("seed") for r in rows} - set(seeds), key=str)
    if bad_seed:
        problems.append(f"rows with seeds not in the manifest: {bad_seed}")

    # 3. same (item, seed) set, same seeds and item count as the baseline (no cherry-picking)
    bpairs = {(r.get("item_id"), r.get("seed")) for r in brows}
    if set(pairs) != bpairs:
        miss, extra = bpairs - set(pairs), set(pairs) - bpairs
        problems.append(f"(item, seed) set differs from the baseline: {len(miss)} missing, {len(extra)} extra")
    if sorted(seeds, key=str) != sorted(bman.get("seeds") or [], key=str):
        problems.append(f"seeds {seeds} differ from the baseline's {bman.get('seeds')}")
    if man.get("items") != bman.get("items"):
        problems.append(f"manifest items {man.get('items')} differ from the baseline's {bman.get('items')}")

    # 4. crashed graders: evaluator failures, or a graded cell that is missing / not a verdict
    if pr.get("evaluator_failures"):
        problems.append(f"{len(pr['evaluator_failures'])} evaluator failure(s): {pr['evaluator_failures'][0]}")
    holes: Counter = Counter()
    for r in rows:
        for m in graded:
            if not isinstance(r.get(m), bool):
                holes[m] += 1
    problems += [f"{n} row(s) with no verdict for graded metric {m} (crashed or removed grader)"
                 for m, n in sorted(holes.items())]

    # 5. reported metrics must equal what the rows say (a hand-edited number is not evidence)
    if rows and not holes:
        problems += _recompute_mismatch(pr, rows, graded, spec)

    # 5b. the baseline is evidence too: an edited baseline row moves every comparison
    if brows:
        bpc = Counter((r.get("item_id"), r.get("seed")) for r in brows)
        if any(n > 1 for n in bpc.values()):
            problems.append("baseline has duplicated (item, seed) rows")
        if base.get("evaluator_failures"):
            problems.append(f"baseline has {len(base['evaluator_failures'])} evaluator failure(s)")
        # a deleted verdict is as bad as an edited one: every graded spec metric must be in every row
        bholes = Counter(m for r in brows for m in graded if not isinstance(r.get(m), bool))
        problems += [f"baseline: {n} row(s) with no verdict for {m}" for m, n in sorted(bholes.items())]
        bmetrics = base.get("metrics") or {}
        problems += [f"baseline: metric {m} missing from its summary" for m in graded if m not in bmetrics]
        if not bholes:
            problems += [f"baseline: {x}" for x in _recompute_mismatch(base, brows, graded, spec)]

    # 6. ratchet: same eval set => thresholds only get stricter. A renamed eval_set_version over the
    #    same items (same eval_set_sha as the baseline) is still the same eval set.
    if base_spec is not None and (base_spec.get("eval_set_version") == spec.get("eval_set_version")
                                  or man.get("eval_set_sha") == bman.get("eval_set_sha")):
        problems += ratchet_problems(spec, base_spec)

    # 7. slice membership must match the baseline's (relabelling hides a slice regression)
    tags, btags = _slice_tags(rows), _slice_tags(brows)
    for item in sorted(set(tags) & set(btags), key=str):
        if tags[item] != btags[item]:
            problems.append(f"{item} slice tags {sorted(tags[item])} differ from the baseline's {sorted(btags[item])}")
    problems += [f"{item} has different slice tags across seeds" for item in _slice_conflicts(rows)]
    return problems


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def threshold_problems(spec: dict) -> list[str]:
    """Every threshold must be a finite number (max_new_failures a non-negative int), every flag a bool."""
    out = []
    entries = [(f"metric {m.get('id')}", m.get("threshold") or {}, m) for m in spec.get("metrics") or []]
    entries += [(f"slice {sl.get('name')}/{sl.get('metric')}", sl, sl) for sl in spec.get("slices") or []]
    for where, th, entry in entries:
        for key in ("min", "max", "max_increase", "max_new_failures"):
            if key in th and not _finite(th[key]):
                out.append(f"{where} threshold {key}={th[key]!r} is not a finite number")
        if "max_new_failures" in th and _finite(th["max_new_failures"]) and                 (th["max_new_failures"] < 0 or th["max_new_failures"] != int(th["max_new_failures"])):
            out.append(f"{where} max_new_failures={th['max_new_failures']!r} must be a non-negative integer")
        for key in ("no_significant_drop",):
            if key in th and not isinstance(th[key], bool):
                out.append(f"{where} {key}={th[key]!r} must be true/false")
        if "blocking" in entry and not isinstance(entry["blocking"], bool):
            out.append(f"{where} blocking={entry['blocking']!r} must be true/false")
    bs = spec.get("bootstrap") or {}
    if "confidence" in bs and not (_finite(bs["confidence"]) and 0 < bs["confidence"] < 1):
        out.append(f"bootstrap confidence={bs['confidence']!r} must be in (0, 1)")
    if "n_resamples" in bs and not (_finite(bs["n_resamples"]) and bs["n_resamples"] >= 1):
        out.append(f"bootstrap n_resamples={bs['n_resamples']!r} must be a positive number")
    return out


def _slice_tags(rows: list[dict]) -> dict[str, frozenset]:
    out: dict[str, frozenset] = {}
    for r in rows:
        out.setdefault(r.get("item_id"), frozenset(r.get("slices") or []))
    return out


def _slice_conflicts(rows: list[dict]) -> list[str]:
    seen: dict[str, set] = {}
    for r in rows:
        seen.setdefault(r.get("item_id"), set()).add(frozenset(r.get("slices") or []))
    return sorted((i for i, t in seen.items() if len(t) > 1), key=str)


def _recompute_mismatch(pr: dict, rows: list[dict], graded: list[str], spec: dict) -> list[str]:
    ids = [m for m in graded if all(m in r for r in rows)]
    out = []
    bad_cost = [f"{r.get('item_id')}@{r.get('seed')}" for r in rows
                if "cost" in r and not (_finite(r["cost"]) and r["cost"] >= 0)]
    if bad_cost:
        out.append(f"{len(bad_cost)} row(s) with a non-finite or negative cost, e.g. {bad_cost[0]}")
    has_cost = all(_finite(r.get("cost")) for r in rows)
    if not has_cost and (pr.get("metrics") or {}).get("cost_per_success") is not None:
        out.append("cost_per_success is reported but rows lack a finite cost to back it")
    try:
        again = aggregate([r if has_cost else {**r, "cost": 0.0} for r in rows], ids,
                          k_values=spec.get("pass_k", []))
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as e:
        return out + [f"metrics cannot be recomputed from rows: {e!r}"]
    for k, v in (pr.get("metrics") or {}).items():
        if k not in again or (k == "cost_per_success" and not has_cost):
            continue
        w = again[k]
        if v is None and w is None:
            continue
        if not (_finite(v) and _finite(w)) or abs(float(v) - float(w)) > 1e-6:
            out.append(f"metric {k} = {v!r} but its rows give {w!r} (edited or stale metrics)")
    return out


def _looser(th: dict, bth: dict) -> list[str]:
    """A bound is looser when it is gone, not a finite number (NaN compares False), or moved the lax way."""
    out = []
    for key, lax in (("max", lambda new, old: new > old), ("min", lambda new, old: new < old),
                     ("max_increase", lambda new, old: new > old), ("max_new_failures", lambda new, old: new > old)):
        if key not in bth or not _finite(bth[key]):
            continue
        new = th.get(key)
        if not _finite(new) or lax(new, bth[key]):
            out.append(f"{key} {bth[key]} -> {new!r}")
    if bth.get("no_significant_drop") and not th.get("no_significant_drop"):
        out.append("no_significant_drop dropped")
    return out


def ratchet_problems(spec: dict, base_spec: dict) -> list[str]:
    """Thresholds in `spec` that are looser than in `base_spec` (same eval set)."""
    out = []
    for key in ("eval_set", "labels", "adversarial_pack", "seeds", "pass_k"):
        if base_spec.get(key) != spec.get(key):
            out.append(f"RATCHET {key} changed {base_spec.get(key)!r} -> {spec.get(key)!r} on the same eval set")
    if (base_spec.get("bootstrap") or {}) != (spec.get("bootstrap") or {}):
        out.append(f"RATCHET bootstrap settings changed {base_spec.get('bootstrap')} -> {spec.get('bootstrap')}")
    now = {m["id"]: m for m in spec.get("metrics", [])}
    for bm in base_spec.get("metrics", []):
        m = now.get(bm["id"])
        if m is None:
            out.append(f"RATCHET metric {bm['id']} removed")
            continue
        if bm.get("blocking") and not m.get("blocking"):
            out.append(f"RATCHET metric {bm['id']} no longer blocking")
        if bm.get("kind") != m.get("kind"):
            out.append(f"RATCHET metric {bm['id']} kind {bm.get('kind')} -> {m.get('kind')}")
        out += [f"RATCHET metric {bm['id']} looser: {x}"
                for x in _looser(m.get("threshold") or {}, bm.get("threshold") or {})]
    snow = {(sl.get("name"), sl.get("metric")): sl for sl in spec.get("slices") or []}
    for bsl in base_spec.get("slices") or []:
        key = (bsl.get("name"), bsl.get("metric"))
        sl = snow.get(key)
        if sl is None:
            out.append(f"RATCHET slice {key[0]}/{key[1]} removed")
            continue
        if bsl.get("blocking") and not sl.get("blocking"):
            out.append(f"RATCHET slice {key[0]}/{key[1]} no longer blocking")
        out += [f"RATCHET slice {key[0]}/{key[1]} looser: {x}" for x in _looser(sl, bsl)]
    return out


def new_failures(pr: dict, base: dict, metric: str, kind: str | None = None) -> list[tuple]:
    """(item_id, seed) rows that were good on main and are bad in the PR. On MockLLM every (item, seed)
    is deterministic (A/A delta = 0), so a flipped row is a regression, not noise; averages and the
    item-level bootstrap can hide a few flipped rows behind rows the PR fixed elsewhere."""
    bad_value = True if kind == "safety" else False     # safety: True = violation; capability: False = fail
    was = {(r.get("item_id"), r.get("seed")): r.get(metric) for r in base.get("rows") or []}
    flipped = []
    for r in pr.get("rows") or []:
        key = (r.get("item_id"), r.get("seed"))
        before, after = was.get(key), r.get(metric)
        if isinstance(before, bool) and before is not bad_value and after is bad_value:
            flipped.append(key)
    return sorted(flipped, key=str)


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocked: list[str] = []
    lines: list[str] = []
    bs = spec.get("bootstrap", {})
    for sl in spec.get("slices") or []:
        name, metric = sl["name"], sl["metric"]
        # Membership comes from the baseline (protected branch) and the PR together, so relabelling
        # an item out of the slice in the PR cannot hide it (integrity_check also flags that).
        members = set(per_item(base, metric, slice_name=name)) | set(per_item(pr, metric, slice_name=name))
        a = {k: v for k, v in per_item(pr, metric).items() if k in members}
        b = {k: v for k, v in per_item(base, metric).items() if k in members}
        val = round(sum(a.values()) / len(a), 4) if a else None
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "min" in sl and val < sl["min"]:
            verdict = "fail"
        elif "max" in sl and val > sl["max"]:
            verdict = "fail"
        keys = sorted(set(a) & set(b))
        if sl.get("no_significant_drop") and keys:
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027),
                                               confidence=bs.get("confidence", 0.95))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if verdict != "ok" and sl.get("blocking"):
            blocked.append(f"slice:{name}/{metric}")
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if sl.get("blocking") else "warn")
        th = {k: sl[k] for k in ("min", "max", "no_significant_drop") if k in sl}
        sid = f"slice:{name}/{metric}"
        lines.append(f"  {sid:<22} {val!s:<8} {('n=' + str(len(a)) + ' ' + json.dumps(th)):<40} {note:<28} {label}")
    return blocked, lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    problems = integrity_check(pr, base, spec, base_spec)
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if "max_new_failures" in th and val is not None:
            n_new = len(new_failures(pr, base, mid, m.get("kind")))
            note = f"new fails {n_new}"
            if n_new > th["max_new_failures"]:
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027),
                                               confidence=bs.get("confidence", 0.95))
            note = (note + " · " if note else "") + f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if verdict != "ok" and m.get("blocking"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr['run_id']} vs main {base['run_id']} · eval set {pr['manifest']['eval_set_sha']}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

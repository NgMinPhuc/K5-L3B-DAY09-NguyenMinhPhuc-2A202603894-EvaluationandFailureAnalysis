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
import re
from collections import Counter
from pathlib import Path

from lab8.console import utf8_stdio
from lab9 import evalset, stats
from lab9.eval import SPEC, aggregate, load_spec, per_item


_TOL = 1e-6                                   # eval.aggregate rounds to 4 dp; a recompute is exact
_RUN_ID = re.compile(r"^E-(?P<shape>.+)-(?P<date>\d{8})-(?P<sha>[0-9a-f]{4})$")
_ROW_META = {"item_id", "seed", "slices", "cost"}


def _num(v: object) -> bool:
    """A real, finite number (bool is not a number here)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _r(x: float) -> float:
    """Round to 9 dp before a threshold comparison so float noise (0.9500000000000001 vs 0.95,
    0.04999999999999993 vs 0.05) can never decide a verdict. Reported means are k/n, far coarser."""
    return round(x, 9)


def _norm_path(p: object) -> str:
    """Posix form without leading './' so './data/x.jsonl' and 'data/x.jsonl' are one path."""
    s = Path(str(p)).as_posix()
    while s.startswith("./"):
        s = s[2:]
    return s


def _same_path(a: object, b: object) -> bool:
    """Same file: equal after normalising, or one is the other with extra leading directories
    (e.g. 'src/data/eval/x.jsonl' from the repo root vs 'data/eval/x.jsonl' from src/)."""
    na, nb = _norm_path(a), _norm_path(b)
    return na == nb or na.endswith("/" + nb) or nb.endswith("/" + na)


def _short(items, n: int = 4) -> str:
    items = list(items)
    if not items:
        return "none"
    return ", ".join(map(str, items[:n])) + (f", ... (+{len(items) - n})" if len(items) > n else "")


def _eval_set_items(manifest: dict, spec: dict | None = None) -> dict[str, list[str]] | None:
    """item_id -> slice tags from the eval-set file the manifest names, but only when the file on
    disk is the very one the report was run on (sha matches). None when it cannot be verified.
    The manifest path is tried as written, then the spec's path when it names the same file."""
    path, sha = manifest.get("eval_set"), manifest.get("eval_set_sha")
    if not isinstance(path, str) or not isinstance(sha, str) or not path or not sha:
        return None
    cands = [path]
    sp = (spec or {}).get("eval_set")
    if isinstance(sp, str) and sp and _same_path(sp, path):
        cands.append(sp)
    for c in cands:
        if not Path(c).is_file():
            continue
        try:
            if evalset.sha12(Path(c)) != sha:
                return None
            return {it["item_id"]: sorted(it.get("slices") or []) for it in evalset.load_jsonl(Path(c))}
        except (OSError, ValueError, KeyError, TypeError):
            return None
    return None


def _manifest_problems(pr: dict, base: dict, spec: dict) -> list[str]:
    out: list[str] = []
    pm, bm = pr.get("manifest"), base.get("manifest")
    if not isinstance(pm, dict):
        return ["manifest: missing or not an object in the PR report"]
    for key in ("eval_set_sha", "items", "seeds"):
        if key not in pm:
            out.append(f"manifest: PR report has no {key!r}")
    if isinstance(bm, dict):
        if "eval_set_sha" in pm and pm.get("eval_set_sha") != bm.get("eval_set_sha"):
            out.append(f"manifest: eval_set_sha {pm.get('eval_set_sha')} differs from baseline {bm.get('eval_set_sha')}")
        if pm.get("eval_set") and bm.get("eval_set") and not _same_path(pm["eval_set"], bm["eval_set"]):
            out.append(f"manifest: eval_set {pm['eval_set']} differs from baseline {bm['eval_set']}")
        if pm.get("llm") != bm.get("llm") and pm.get("llm") and bm.get("llm"):
            out.append(f"manifest: llm {pm['llm']!r} differs from baseline {bm['llm']!r}")
    sv, ev = spec.get("metric_spec_version"), pm.get("metric_spec_version")
    if sv is not None and ev is not None and str(sv) != str(ev):
        out.append(f"manifest: report was run with spec version {ev}, gated with {sv}")
    if spec.get("eval_set") and pm.get("eval_set") and not _same_path(spec["eval_set"], pm["eval_set"]):
        out.append(f"manifest: report eval set {pm['eval_set']} is not the spec's {spec['eval_set']}")
    rid = pr.get("run_id")
    if rid is not None:
        m = _RUN_ID.match(str(rid))
        if not m:
            out.append(f"run_id {rid!r} does not have the shape E-<shape>-<yyyymmdd>-<sha4>")
        else:
            if pm.get("eval_set_sha") and not str(pm["eval_set_sha"]).startswith(m["sha"]):
                out.append(f"run_id {rid} does not carry the manifest eval_set_sha {pm['eval_set_sha']}")
            if pr.get("shape") and pr["shape"] != m["shape"]:
                out.append(f"run_id {rid} names shape {m['shape']!r} but the report says {pr['shape']!r}")
    return out


def _rowset_problems(pr: dict, base: dict, spec: dict | None = None) -> list[str]:
    """The PR must contain exactly items x seeds rows: no dropped, extra or duplicated (item, seed)."""
    out: list[str] = []
    rows, brows = pr.get("rows"), base.get("rows")
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        return ["rows: missing or not a list of objects"]
    pm, bm = pr.get("manifest") or {}, base.get("manifest") or {}
    seeds = pm.get("seeds")
    if not isinstance(seeds, list) or not all(isinstance(s, int) and not isinstance(s, bool) for s in seeds):
        return ["manifest: seeds is not a list of integers"]
    if len(set(seeds)) != len(seeds):
        out.append(f"manifest: duplicate seeds in {seeds}")
    if isinstance(bm.get("seeds"), list) and set(seeds) != set(bm["seeds"]):
        out.append(f"seeds {sorted(set(seeds))} differ from baseline {sorted(set(bm['seeds']))} (cherry-picked seeds)")
    badkey = [f"{r.get('item_id')!r}@{r.get('seed')!r}" for r in rows
              if not isinstance(r.get("item_id"), str)
              or not isinstance(r.get("seed"), int) or isinstance(r.get("seed"), bool)]
    if badkey:
        return [f"rows: item_id must be a string and seed an integer, got {len(badkey)} row(s) like {_short(badkey)}"]
    keys = [(r.get("item_id"), r.get("seed")) for r in rows]
    dup = sorted((k for k, n in Counter(keys).items() if n > 1), key=str)
    if dup:
        out.append(f"{len(keys) - len(set(keys))} duplicated row(s) for (item, seed): {_short(dup)}")
    # What the PR should contain: the eval set's items (file, when verifiable) else the baseline's.
    file_items = _eval_set_items(pm, spec)
    base_items = {r.get("item_id") for r in (brows or []) if isinstance(r, dict) and isinstance(r.get("item_id"), str)}
    items_pr = {k[0] for k in keys}
    if file_items is not None and items_pr != set(file_items):
        out.append(f"item set differs from the eval set file: missing {_short(sorted(set(file_items) - items_pr))},"
                   f" extra {_short(sorted(items_pr - set(file_items), key=str))}")
    if base_items and items_pr != base_items:
        out.append(f"item set differs from baseline: missing {_short(sorted(base_items - items_pr))},"
                   f" extra {_short(sorted(items_pr - base_items, key=str))}")
    expected_items = set(file_items) if file_items is not None else (base_items or items_pr)
    want = {(i, s) for i in expected_items for s in seeds}
    got = set(keys)
    if want != got:
        miss, extra = sorted(want - got, key=str), sorted(got - want, key=str)
        out.append(f"row set is not items x seeds: {len(miss)} missing {_short(miss)}; {len(extra)} unexpected {_short(extra)}")
    if isinstance(bm.get("seeds"), list) and isinstance(brows, list):
        bseed_rows = {r.get("seed") for r in brows if isinstance(r, dict) and isinstance(r.get("seed"), int)}
        if {k[1] for k in keys} != bseed_rows:
            out.append(f"row seeds {sorted({k[1] for k in keys}, key=str)} differ from baseline row seeds {sorted(bseed_rows, key=str)}")
    n_items = pm.get("items")
    if isinstance(n_items, int) and not isinstance(n_items, bool):
        if file_items is not None and n_items != len(file_items):
            out.append(f"manifest items={n_items} but the eval set has {len(file_items)}")
        if len(rows) != n_items * len(seeds):
            out.append(f"{len(rows)} rows != manifest items {n_items} x {len(seeds)} seeds = {n_items * len(seeds)}")
    elif "items" in pm:
        out.append(f"manifest: items {n_items!r} is not an integer")
    # relabelling an item out of a slice hides its regression from the slice gate
    slices_pr: dict[object, set[tuple]] = {}
    for r in rows:
        s = r.get("slices")
        if not isinstance(s, list):
            out.append(f"row {r.get('item_id')}@{r.get('seed')}: slices is not a list")
            break
        slices_pr.setdefault(r.get("item_id"), set()).add(tuple(sorted(map(str, s))))
    ref = {i: [tuple(sorted(map(str, r.get("slices") or []))) for r in brows if r.get("item_id") == i][:1]
           for i in base_items} if isinstance(brows, list) else {}
    for item, tags in sorted(slices_pr.items(), key=lambda kv: str(kv[0])):
        if len(tags) > 1:
            out.append(f"item {item}: slice tags differ between its rows {sorted(tags)}")
            continue
        (mine,) = tags
        for src, want_tags in (("baseline", (ref.get(item) or [None])[0]),
                               ("eval set", None if file_items is None or item not in file_items else tuple(file_items[item]))):
            if want_tags is not None and mine != want_tags:
                out.append(f"item {item}: slice tags {list(mine)} differ from {src} {list(want_tags)} (relabelled out of a slice?)")
    return out


def _columns(pr: dict, base: dict, spec: dict) -> list[str]:
    """Row-level graded metrics: spec metrics that appear as a column in the baseline or the PR rows."""
    ids = [m["id"] for m in spec.get("metrics") or [] if isinstance(m, dict) and "id" in m]
    seen = {k for rep in (pr, base) for r in rep.get("rows") or [] if isinstance(r, dict) for k in r}
    return [m for m in ids if m in seen and m not in _ROW_META]


def _value_problems(pr: dict, base: dict, cols: list[str], spec: dict) -> list[str]:
    out: list[str] = []
    fails = pr.get("evaluator_failures")
    if not isinstance(fails, list):
        out.append("evaluator_failures: missing or not a list (a report always records them)")
    elif fails:
        out.append(f"{len(fails)} evaluator failure(s), even on a green run a crashed grader is not a pass: {_short(fails, 2)}")
    rows = [r for r in pr.get("rows") or [] if isinstance(r, dict)]
    bad: dict[str, list[str]] = {}
    for r in rows:
        tag = f"{r.get('item_id')}@{r.get('seed')}"
        for c in cols:
            if c not in r:
                bad.setdefault(f"column {c!r} is missing", []).append(tag)
            elif not isinstance(r[c], bool):
                bad.setdefault(f"{c!r} is {r[c]!r} (not a bool; a crashed grader drops out of the mean)", []).append(tag)
    base_cost = any(isinstance(r, dict) and "cost" in r for r in base.get("rows") or [])
    for r in rows:
        if "cost" in r or base_cost:
            if not _num(r.get("cost")) or r["cost"] < 0:
                bad.setdefault(f"cost is {r.get('cost')!r}", []).append(f"{r.get('item_id')}@{r.get('seed')}")
    for why, tags in bad.items():
        out.append(f"row value: {why} in {len(tags)} row(s): {_short(tags)}")
    metrics = pr.get("metrics")
    if not isinstance(metrics, dict):
        return out + ["metrics: missing or not an object"]
    for k, v in metrics.items():
        if v is None and k in cols:
            out.append(f"metric {k} is None although every row is graded")
        elif v is not None and not _num(v):
            out.append(f"metric {k} = {v!r} is not a finite number")
    spec_ids = {m["id"] for m in spec.get("metrics") or [] if isinstance(m, dict) and "id" in m}
    gone = sorted(k for k in (base.get("metrics") or {}) if k in spec_ids and k not in metrics)
    if gone:
        out.append(f"metric(s) {_short(gone)} are in the baseline but were dropped from the PR report")
    return out


def _recompute_problems(pr: dict, cols: list[str], spec: dict) -> list[str]:
    """Aggregates must be what the rows say: catches hand-edited / beautified metrics."""
    rows, metrics = pr.get("rows") or [], pr.get("metrics") or {}
    if not cols or not all(isinstance(r, dict) and all(isinstance(r.get(c), bool) for c in cols) for r in rows):
        return []                                  # unusable rows were already reported
    ks = set(spec.get("pass_k") or [])
    for name in metrics:
        m = re.fullmatch(r"pass_(?:at|hat)_(\d+)", name)
        if m:
            ks.add(int(m[1]))
    has_cost = all(_num(r.get("cost")) for r in rows)
    try:
        fresh = aggregate([{**r, "cost": r["cost"] if has_cost else 0.0} for r in rows], cols, k_values=sorted(ks))
    except Exception as exc:                       # noqa: BLE001 - any failure here is itself a bad report
        return [f"aggregates cannot be recomputed from the rows: {type(exc).__name__}: {exc}"]
    out = []
    for k, want in fresh.items():
        if k not in metrics or (k == "cost_per_success" and not has_cost):
            continue
        if k != "pass_rate" and k.startswith(("pass_5", "pass_at_", "pass_hat_")) and "pass_rate" not in cols:
            continue
        got = metrics[k]
        same = (got is None and want is None) or (_num(got) and want is not None and abs(got - want) <= _TOL)
        if not same:
            out.append(f"metric {k} reported {got!r} but the rows give {want!r}")
    return out


_THRESHOLD_KEYS = ("min", "max", "max_increase", "max_drop")


def _spec_problems(spec: object, who: str = "spec") -> list[str]:
    """Spec sanity, run on every gate invocation: a threshold that is NaN, inf, a string or a bool
    makes every comparison False (so nothing ever fails) or raises, i.e. it silently disables the gate."""
    if not isinstance(spec, dict):
        return [f"{who}: not an object"]
    out: list[str] = []

    def thresholds(where: str, d: object) -> None:
        if not isinstance(d, dict):
            out.append(f"{who} {where}: threshold is not an object")
            return
        for key in _THRESHOLD_KEYS:
            if key in d and not _num(d[key]):
                out.append(f"{who} {where}: {key} = {d[key]!r} is not a finite number")

    for field, idkey in (("metrics", "id"), ("slices", "name")):
        entries = spec.get(field)
        if entries is None:
            if field == "metrics":
                out.append(f"{who}: metrics is missing")
            continue
        if not isinstance(entries, list):
            out.append(f"{who}: {field} is not a list")
            continue
        for i, e in enumerate(entries):
            if not isinstance(e, dict) or not isinstance(e.get(idkey), str):
                out.append(f"{who} {field}[{i}]: not an object with a string {idkey!r}")
                continue
            where = f"{field[:-1]} {e[idkey]}"
            if field == "metrics":
                if e.get("threshold") is not None:
                    thresholds(where, e["threshold"])
            else:
                thresholds(where, {k: e[k] for k in _THRESHOLD_KEYS if k in e})
    bs = spec.get("bootstrap")
    if bs is not None:
        n = bs.get("n_resamples") if isinstance(bs, dict) else None
        if not isinstance(bs, dict) or (n is not None and (not isinstance(n, int) or isinstance(n, bool) or n < 1)):
            out.append(f"{who} bootstrap: not an object with a positive integer n_resamples")
    return out


def _ratchet_problems(spec: dict, base_spec: dict) -> list[str]:
    """RATCHET: on the same eval set a spec may only get stricter than main's."""
    out: list[str] = []

    def loosened(where: str, b: dict, p: dict) -> None:
        for key in ("min", "max_increase", "max", "max_drop"):
            if key not in b or not _num(b[key]):
                continue
            if key not in p:
                out.append(f"RATCHET {where}: {key} {b[key]} was removed")
            elif not _num(p[key]):
                out.append(f"RATCHET {where}: {key} {b[key]} -> {p[key]!r} is not a finite number")
            elif (_r(p[key]) < _r(b[key])) if key == "min" else (_r(p[key]) > _r(b[key])):
                out.append(f"RATCHET {where}: {key} loosened {b[key]} -> {p[key]}")
        if b.get("no_significant_drop") and not p.get("no_significant_drop"):
            out.append(f"RATCHET {where}: no_significant_drop was dropped")

    bv, pv = base_spec.get("eval_set_version"), spec.get("eval_set_version")
    if bv is not None and pv is not None and bv != pv:
        out.append(f"RATCHET eval_set_version changed {bv} -> {pv} on an unchanged eval set")
    pm = {m["id"]: m for m in spec.get("metrics") or []}
    for bmet in base_spec.get("metrics") or []:
        mid = bmet["id"]
        pmet = pm.get(mid)
        if pmet is None:
            out.append(f"RATCHET metric {mid}: removed from the spec")
            continue
        if bmet.get("blocking") and not pmet.get("blocking"):
            out.append(f"RATCHET metric {mid}: no longer blocking")
        if bmet.get("kind") == "safety" and pmet.get("kind") != "safety":
            out.append(f"RATCHET metric {mid}: no longer a safety metric")
        loosened(f"metric {mid}", bmet.get("threshold") or {}, pmet.get("threshold") or {})
    ps = {s["name"]: s for s in spec.get("slices") or []}
    for bsl in base_spec.get("slices") or []:
        name = bsl["name"]
        psl = ps.get(name)
        if psl is None:
            out.append(f"RATCHET slice {name}: removed from the spec")
            continue
        if bsl.get("blocking") and not psl.get("blocking"):
            out.append(f"RATCHET slice {name}: no longer blocking")
        mets = lambda s: set(s.get("metrics") or ([s["metric"]] if s.get("metric") else []))
        if mets(bsl) - mets(psl):
            out.append(f"RATCHET slice {name}: metric(s) {sorted(mets(bsl) - mets(psl))} no longer gated")
        loosened(f"slice {name}", bsl, psl)
    for key in ("seeds", "pass_k"):                # fewer seeds / smaller k = less evidence
        lost = set(base_spec.get(key) or []) - set(spec.get(key) or [])
        if lost:
            out.append(f"RATCHET {key}: {sorted(lost)} removed")
    # Bootstrap settings decide the CI that no_significant_drop blocks on (block when the paired CI
    # lies entirely on the wrong side of 0). A narrower CI (lower confidence) blocks more but with
    # more false alarms; a wider CI (higher confidence) is more likely to touch 0 and so blocks
    # LESS; fewer resamples gives a noisier CI; a different rng or method yields a different CI on
    # the same data. Loosening therefore has no single direction, and any change lets an author
    # shop for a CI that lets a regression through. So on the same eval set: confidence, method and
    # rng must be unchanged, n_resamples may only grow, and nothing present in main may be removed.
    bb, pb = base_spec.get("bootstrap"), spec.get("bootstrap")
    if bb:
        if not isinstance(pb, dict):
            out.append("RATCHET bootstrap: block was removed")
        else:
            for key in sorted(bb):
                if key not in pb:
                    out.append(f"RATCHET bootstrap: {key} {bb[key]} was removed")
                elif key == "n_resamples":
                    try:
                        fewer = pb[key] < bb[key]
                    except TypeError:
                        fewer = True
                    if fewer:
                        out.append(f"RATCHET bootstrap: n_resamples loosened {bb[key]} -> {pb[key]}")
                elif pb[key] != bb[key]:
                    out.append(f"RATCHET bootstrap: {key} changed {bb[key]} -> {pb[key]}")
    return out


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Everything is derived from the report's own manifest, the baseline and the eval-set file the
    manifest names, never from variant or trap names:
      - manifest: eval-set sha/path, spec version, llm and run_id shape agree with baseline and spec
      - row set: exactly items x seeds (no dropped, extra or duplicated (item, seed)), the same
        items and seeds as the baseline, and the same slice tags per item as baseline / eval set
      - values: every graded row value is a real bool, cost is finite, evaluator_failures is an
        empty list (even on a green run), no metric is None/NaN/inf or dropped
      - aggregates: every reported metric equals the recompute from the rows (pass^k included)
      - spec: every threshold is a finite real number (never NaN/inf/str/bool), metrics/slices are well formed
      - ratchet (base_spec given, same eval set path): thresholds, blocking flags, no_significant_drop, slices, seeds
        and pass_k may only get stricter, never be removed; bootstrap confidence/method/rng must
        stay unchanged and n_resamples may not shrink"""
    spec_problems = _spec_problems(spec)
    if base_spec is not None:
        spec_problems += _spec_problems(base_spec, "base spec")
    problems = list(spec_problems)
    problems += _manifest_problems(pr, base, spec)
    problems += _rowset_problems(pr, base, spec)
    cols = _columns(pr, base, spec)
    problems += _value_problems(pr, base, cols, spec)
    problems += _recompute_problems(pr, cols, spec)
    if base_spec is not None and not spec_problems and not (base_spec.get("eval_set") and spec.get("eval_set")
                                      and not _same_path(base_spec["eval_set"], spec["eval_set"])):
        # A spec for a different eval set (v2 vs a v1 base) starts a new baseline: nothing to ratchet
        # against. The report/baseline manifests must still name the spec's eval set (checked above).
        problems += _ratchet_problems(spec, base_spec)
    return problems


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric"|"metrics", "min"?, "max"?, "max_drop"?,
    "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min" (above "max"), if the slice mean fell more than "max_drop" below the
        baseline's on the same items, or if no_significant_drop and the paired CI over the slice's
        items lies entirely on the wrong side of 0
      - fail if the slice has items in the baseline but none/fewer in the PR (an item relabelled
        out of a slice hides its regression)
    Direction: a metric whose global threshold is max/max_increase (violation counters such as
    approval_gate_skipped) is lower-is-better; every other metric is higher-is-better.
    A slice with no rows in both reports is skipped (nothing to gate), never silently "passed" once
    the baseline has rows for it.
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    bs = spec.get("bootstrap", {})
    lower_better = {m["id"] for m in spec.get("metrics", [])
                    if {"max", "max_increase"} & set(m.get("threshold") or {})}
    blocked: list[str] = []
    lines: list[str] = []
    for sl in spec.get("slices") or []:
        name = sl["name"]
        metrics = list(sl.get("metrics") or ([sl["metric"]] if sl.get("metric") else []))
        for mid in metrics:
            a = per_item(pr, mid, slice_name=name)
            b = per_item(base, mid, slice_name=name)
            ident = f"slice:{name}/{mid}"
            label_fail = "BLOCK" if sl.get("blocking") else "warn"
            if not a and not b:
                lines.append(f"  {ident:<34} {'-':<8} no rows in PR or baseline: not gated")
                continue
            reasons: list[str] = []
            val = sum(a.values()) / len(a) if a else None
            note = ""
            if val is None:
                reasons.append("no rows in PR")
            else:
                gone = sorted(set(b) - set(a))
                if gone:
                    reasons.append(f"{len(gone)} baseline item(s) missing from the slice in PR")
                keys = sorted(set(a) & set(b))
                sign = -1.0 if mid in lower_better else 1.0   # sign * delta < 0  <=>  worse
                if "min" in sl and _r(val) < _r(sl["min"]):
                    reasons.append(f"mean {val:.3f} < min {sl['min']}")
                if "max" in sl and _r(val) > _r(sl["max"]):
                    reasons.append(f"mean {val:.3f} > max {sl['max']}")
                if keys:
                    pa, pb = [a[k] for k in keys], [b[k] for k in keys]
                    drop = sign * (sum(pb) - sum(pa)) / len(keys)     # > 0 means the PR is worse
                    d, lo, hi = stats.paired_bootstrap(pa, pb, n_resamples=bs.get("n_resamples", 1000),
                                                       rng=bs.get("rng", 2027))
                    note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}] n={len(keys)}"
                    if "max_drop" in sl and _r(drop) > _r(sl["max_drop"]):
                        reasons.append(f"drop {drop:.3f} > max_drop {sl['max_drop']}")
                    if sl.get("no_significant_drop") and (_r(hi) < 0 if sign > 0 else _r(lo) > 0):
                        reasons.append("paired CI entirely on the wrong side of 0")
            if reasons:
                if sl.get("blocking"):
                    blocked.append(ident)
            verdict = label_fail if reasons else "ok"
            vtxt = "-" if val is None else f"{val:.4f}"
            lines.append(f"  {ident:<34} {vtxt:<8} {note:<36} {verdict}" + (f"  ({'; '.join(reasons)})" if reasons else ""))
    return blocked, lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    """Gate a PR report. Never raises on malformed reports/specs: that is an INTEGRITY block."""
    try:
        return _check(pr, base, spec, base_spec)
    except Exception as exc:                       # noqa: BLE001 - last resort: a broken input must not crash into a pass
        return ["integrity"], [f"  INTEGRITY: malformed input: {type(exc).__name__}: {exc}"]


def _check(pr: dict, base: dict, spec: dict, base_spec: dict | None) -> tuple[list[str], list[str]]:
    pm, bm = (pr.get("manifest") if isinstance(pr, dict) else None), (base.get("manifest") if isinstance(base, dict) else None)
    if (isinstance(pm, dict) and isinstance(bm, dict) and pm.get("eval_set_sha") is not None
            and bm.get("eval_set_sha") is not None and pm["eval_set_sha"] != bm["eval_set_sha"]):
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    problems = integrity_check(pr, base, spec, base_spec)
    # A missing run_id is not a tampering signal on its own (synthetic in-process reports have none);
    # a present run_id is still shape-checked inside integrity_check, and main() prints it via .get.
    for who, rep in (("PR", pr), ("baseline", base)):
        rid = rep.get("run_id")
        if rid is not None and not isinstance(rid, str):
            problems.append(f"run_id: not a string in the {who} report")
    if problems:
        return ["integrity"], [f"  INTEGRITY: {p}" for p in problems]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold") or {}, pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and _r(val) > _r(th["max"]):
            verdict = "fail"
        elif "min" in th and _r(val) < _r(th["min"]):
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if _r(val) > _r(base["metrics"][mid] * (1 + th["max_increase"])):
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if _r(hi) < 0:
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
    pm = pr.get("manifest") if isinstance(pr, dict) else None
    ident = lambda rep: rep.get("run_id") if isinstance(rep, dict) else None
    print(f"gate: PR {ident(pr)} vs main {ident(base)} · eval set {pm.get('eval_set_sha') if isinstance(pm, dict) else None}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

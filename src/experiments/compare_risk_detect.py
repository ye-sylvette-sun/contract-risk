"""Score a risk-detection run, per panel:

  ROC-AUC     ranking quality, threshold-free
  precision   of what we flagged, how much was really litigated
  recall      of what was litigated, how much we flagged
  flag rate   what share of the contract a reader is asked to read

The flag rate keeps the other two honest: at 2% prevalence a threshold that
flags a third of the contract can post a respectable recall and be useless.

The figure is the same sweep drawn out: `plot_risk_detect_thresholds.py`.

One run is the normal case. `--against PATH` adds a second and scores both over
the provisions they share, which is how two prompts, or two repeats of one, are
compared without the difference being in which contracts each covered.

Usage:
    python src/experiments/compare_risk_detect.py
    python src/experiments/compare_risk_detect.py --against <other>_preds.csv
    python src/experiments/compare_risk_detect.py --json output/risk_detect_scores.json
"""
import argparse
import csv
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "runs", Path(__file__).with_name("runs.py"))
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

RUNS = {"agent": lib.OUT / "risk_detect_agent_preds.csv"}
FLAG = 0.5
RECALL_TARGETS = (0.70, 0.80, 0.90)

PANELS = {
    "risky vs not": "risky",
    "risk type 1 vs not": "type1",
    "risk type 2 vs not": "type2",
}


def load(path):
    """(contract_id, clause_id) -> row, with unjudged provisions kept.

    Silence is a prediction of "not risky" at probability 0, never a dropped
    row: dropping it would forgive the omission, and on a positive it would
    inflate recall. `preds.csv` is append-only, so a scored row always wins over
    a blank one for the same provision.
    """
    keep = {}
    for r in api.load_rows(path):
        k = (r["contract_id"], r["clause_id"])
        prev = keep.get(k)
        if prev is not None and (prev["ok"] == "1" or r["ok"] != "1"):
            continue
        keep[k] = r
    unscored = 0
    for r in keep.values():
        if r["ok"] != "1":
            unscored += 1
            r["prob_type1"] = r["prob_type2"] = "0"
            r["pred"] = "not_risky"
    return keep, unscored


def scored(rows, which):
    """(score, label) per row for one panel.

    The two risk-type panels are one-vs-rest: for risk type 1 a clause that is
    only risk type 2 counts as a negative, and the other way round. That is the
    question the two probabilities are actually asked — each is an independent
    judgement about its own risk type, not a share of one distribution.

    The two are NOT exclusive. A clause whose case was filed under several
    Westlaw keys can be gold for both, and it then counts as a positive in both
    panels, which is why the labels come from `gold_type1` / `gold_type2` rather
    than from one winner-takes-all `gold` string.
    """
    if which == "risky":
        return ([max(api._f(r["prob_type1"]), api._f(r["prob_type2"])) for r in rows],
                [1 if r["gold"] != "not_risky" else 0 for r in rows])
    col = "prob_type1" if which == "type1" else "prob_type2"
    return ([api._f(r[col]) for r in rows],
            [int(r.get(f"gold_{which}") in (1, "1")) for r in rows])


def type_metrics(rows):
    """Two ways of asking whether the risk type was right, over distinct defects.

    `wins`   the gold type outscored the other. What a single-label reading
             asks, and what the type panels' winner is.
    `named`  the gold type got any probability at all — the model listed an
             issue of that type on that provision, wherever it ranked.

    They come apart because the output is a LIST. A provision can carry several
    real defects; the court construed one of them, and the model may name that
    one at 0.18 and a different, equally real one at 0.44. `wins` scores that as
    a type error, `named` as a hit. Neither is the whole truth — `wins` is the
    stricter, `named` the more forgiving — so both are reported, and the gap
    between them is how much of the type error is really a ranking difference.

    Deduplicated on the gold issue text, because one recorded defect can sit on
    several provisions of one contract: `359FSupp3d268_leases` repeats a
    construed clause five times, and counting it five times would weight the
    score by how often a drafter copied a paragraph. A defect counts as found if
    ANY provision carrying it was scored that way.

    Returns {1: {...}, 2: {...}}, empty where the dataset is not on disk — the
    gold issue text lives there, not in `preds.csv`.
    """
    ds = lib.OUT / "dataset.csv"
    if not ds.exists():
        return {}
    gold = {(r["contract_id"], r["clause_id"]): json.loads(r["issues"] or "[]")
            for r in api.load_rows(ds)}
    out = {}
    for t in (1, 2):
        other, seen = 3 - t, {}
        for r in rows:
            k = (r["contract_id"], r["clause_id"])
            p_own = api._f(r[f"prob_type{t}"])
            p_oth = api._f(r[f"prob_type{other}"])
            for g in gold.get(k, []):
                if not str(g.get("risk_type", "")).startswith(str(t)):
                    continue
                w, n = seen.get(g["issue"].strip(), (False, False))
                seen[g["issue"].strip()] = (
                    w or (p_own >= p_oth and max(p_own, p_oth) > 0),
                    n or p_own > 0)
        if seen:
            out[t] = {"n": len(seen),
                      "wins": sum(1 for w, _ in seen.values() if w),
                      "named": sum(1 for _, n in seen.values() if n)}
    return out


def at(scores, labels, t):
    """Precision, recall and flag rate at one threshold."""
    flagged = [s >= t for s in scores]
    n_flag = sum(flagged)
    tp = sum(1 for f, y in zip(flagged, labels) if f and y)
    return (tp / n_flag if n_flag else 0.0,
            tp / sum(labels) if any(labels) else 0.0,
            n_flag / len(scores))


def cheapest_at_recall(scores, labels, target):
    """The highest threshold that still reaches `target` recall.

    Highest, not lowest: the question is what a reader pays, and a higher
    threshold flags less. Returns None if the target is out of reach.
    """
    best = None
    for i in range(101):
        t = i / 100
        p, r, f = at(scores, labels, t)
        if r >= target:
            best = (t, p, r, f)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", metavar="PATH", help="also write the numbers here")
    ap.add_argument("--agent", metavar="PATH", help="the run to score")
    ap.add_argument("--against", metavar="PATH",
                    help="a second run, scored over the shared provisions")
    args = ap.parse_args()
    if args.agent:
        RUNS["agent"] = Path(args.agent)
    if args.against:
        RUNS["other"] = Path(args.against)

    loaded, unscored = {}, {}
    for run, path in RUNS.items():
        if not path.exists():
            print(f"! {run}: {path} does not exist")
            return
        loaded[run], unscored[run] = load(path)

    # Only provisions EVERY run covers. A partial run against a full one would
    # differ as much in which contracts each covered as in anything about the
    # method.
    shared = sorted(set.intersection(*(set(v) for v in loaded.values())))
    rows = {run: [loaded[run][k] for k in shared] for run in RUNS}
    contracts = {r["contract_id"] for r in rows["agent"]}

    what = "judged by every run" if len(RUNS) > 1 else "judged"
    print(f"{len(shared)} provisions {what}, in {len(contracts)} contract(s)")
    for run in RUNS:
        print(f"  {run:8} {len(loaded[run]):6} row(s), "
              f"{len(loaded[run]) - len(shared)} not shared, "
              f"{unscored[run]} unjudged (scored not_risky at 0)")

    res = {"n_shared": len(shared), "n_contracts": len(contracts), "panels": {}}
    for title, which in PANELS.items():
        print(f"\n=== {title} ===")
        print(f"{'run':10}{'ROC-AUC':>10}{'P@0.5':>8}{'R@0.5':>8}{'flagged':>10}"
              f"{'positives':>11}")
        res["panels"][which] = {}
        for run in RUNS:
            s, y = scored(rows[run], which)
            p, r, f = at(s, y, FLAG)
            res["panels"][which][run] = {
                "roc_auc": api.roc_auc(s, y), "precision": p, "recall": r,
                "flag_rate": f, "positives": sum(y), "n": len(s)}
            print(f"{run:10}{api.roc_auc(s, y):10.3f}{p:8.2f}{r:8.2f}"
                  f"{f:9.1%}{sum(y):11}")

    print("\n=== risk type, over distinct gold defects ===")
    print("  `wins`  the gold type outscored the other")
    print("  `named` the gold type was listed at all, wherever it ranked")
    res["type"] = {}
    hdr = False
    for run in RUNS:
        tm = type_metrics(rows[run])
        if not tm:
            print("  (output/dataset.csv not found — skipped)")
            break
        res["type"][run] = tm
        if not hdr:
            print(f"\n{'run':10}{'type':>6}{'defects':>9}{'wins':>12}{'named':>12}")
            hdr = True
        for t, m in sorted(tm.items()):
            print(f"{run:10}{t:>6}{m['n']:>9}"
                  f"{m['wins']:>7} {100*m['wins']/m['n']:>3.0f}%"
                  f"{m['named']:>7} {100*m['named']/m['n']:>3.0f}%")

    print("\n=== what each recall target costs (risky vs not) ===")
    print(f"{'target':>8}{'run':>10}{'threshold':>11}{'precision':>11}"
          f"{'flagged':>10}{'recall':>9}")
    res["at_recall"] = {}
    for target in RECALL_TARGETS:
        res["at_recall"][f"{target:.2f}"] = {}
        for run in RUNS:
            s, y = scored(rows[run], "risky")
            got = cheapest_at_recall(s, y, target)
            if not got:
                print(f"{target:8.0%}{run:>10}{'  unreachable':>32}")
                continue
            t, p, r, f = got
            res["at_recall"][f"{target:.2f}"][run] = {
                "threshold": t, "precision": p, "recall": r, "flag_rate": f}
            print(f"{target:8.0%}{run:>10}{t:11.2f}{p:11.3f}{f:10.1%}{r:9.2f}")

    print("\nfigure: python src/experiments/plot_risk_detect_thresholds.py")

    if args.json:
        Path(args.json).write_text(json.dumps(res, indent=2, default=str),
                                   encoding="utf-8")
        print(f"written to {args.json}")


if __name__ == "__main__":
    main()

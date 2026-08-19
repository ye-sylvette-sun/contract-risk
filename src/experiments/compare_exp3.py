"""The two runs side by side, per panel:

  ROC-AUC     ranking quality, threshold-free
  precision   of what we flagged, how much was really litigated
  recall      of what was litigated, how much we flagged
  flag rate   what share of the contract a reader is asked to read

The flag rate keeps the other two honest: at 2% prevalence a threshold that
flags a third of the contract can post a respectable recall and be useless.

The figure is the same sweep drawn out: `plot_exp3_thresholds.py --run <run>`.

Usage:
    python src/experiments/compare_exp3.py
    python src/experiments/compare_exp3.py --json output/exp3_comparison.json
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
    "exp3_llm_api", Path(__file__).with_name("exp3_llm_api.py"))
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

RUNS = {"llm_api": lib.OUT / "exp3_llm_api_preds.csv",
        "agent": lib.OUT / "exp3_agent_preds.csv"}
FLAG = 0.5
RECALL_TARGETS = (0.70, 0.80, 0.90)

PANELS = {
    "risky vs not": "risky",
    "type 1 vs not": "cat1",
    "type 2 vs not": "cat2",
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
            r["prob_cat1"] = r["prob_cat2"] = "0"
            r["pred"] = "not_risky"
    return keep, unscored


def scored(rows, which):
    """(score, label) per row for one panel.

    The two type panels are one-vs-rest: for type 1 a type-2 clause counts as a
    negative, and the other way round. That is the question the two
    probabilities are actually asked — each is an independent judgement about
    its own category, not a share of one distribution.
    """
    if which == "risky":
        return ([max(api._f(r["prob_cat1"]), api._f(r["prob_cat2"])) for r in rows],
                [1 if r["gold"] != "not_risky" else 0 for r in rows])
    col = "prob_cat1" if which == "cat1" else "prob_cat2"
    return ([api._f(r[col]) for r in rows],
            [1 if r["gold"] == f"risky_{which}" else 0 for r in rows])


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
    # Either side can be pointed elsewhere, e.g. at the superseded run kept on
    # `legacy_agent_experiment_8.17`.
    ap.add_argument("--agent", metavar="PATH")
    ap.add_argument("--llm-api", metavar="PATH", dest="llm_api")
    args = ap.parse_args()
    if args.agent:
        RUNS["agent"] = Path(args.agent)
    if args.llm_api:
        RUNS["llm_api"] = Path(args.llm_api)

    loaded, unscored = {}, {}
    for run, path in RUNS.items():
        if not path.exists():
            print(f"! {run}: {path} does not exist")
            return
        loaded[run], unscored[run] = load(path)

    # Only provisions BOTH runs cover. A partial run against a full one would
    # differ as much in which contracts each covered as in anything about the
    # method.
    shared = sorted(set(loaded["llm_api"]) & set(loaded["agent"]))
    rows = {run: [loaded[run][k] for k in shared] for run in RUNS}
    contracts = {r["contract_id"] for r in rows["agent"]}

    print(f"{len(shared)} provisions judged by both runs, in "
          f"{len(contracts)} contract(s)")
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

    print("\nfigure: python src/experiments/plot_exp3_thresholds.py --run "
          "{llm_api,agent}")

    if args.json:
        Path(args.json).write_text(json.dumps(res, indent=2, default=str),
                                   encoding="utf-8")
        print(f"written to {args.json}")


if __name__ == "__main__":
    main()

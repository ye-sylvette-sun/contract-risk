"""Threshold-sweep figure at the ISSUE level, with alignment required.

The risk-detection figure scores a provision: right or wrong about whether a
court construed it. This one scores an **issue** — a named defect — and counts
it correct when both hold:

    the provision was construed  AND  the named defect is one the court
    actually construed (alignment >= 0.5)

so a provision flagged for the wrong reason is a false positive here even
though the other figure counts it as a hit. The gap between the two curves is
the price of demanding the right reason.

**Risk type is NOT part of the test.** The alignment judge is shown neither
side's type, and a candidate is not filtered by it: the dataset's labels come
from the case's Westlaw key rather than from the passage, so gating on them
discards matches where both sides name the same defect and classify it
differently. Type agreement is measured separately, over the matched pairs.

**The two recalls have different denominators, and the figure says so.** Strict
recall is over the court's own defects: step 2 records each one separately, and
the judge reports WHICH it matched, so a found defect is counted once. Lenient
recall drops the alignment test, and without a match there is no way to say
which defect was reached, so it can only count PROVISIONS the court construed.

**Null-text entries are excluded from the universe.** An entry with `issue:
null` states a probability without naming a defect, so it can never be right for
the right reason and scoring it here would only dilute the denominator.

Gold issues the check could never reach — the model named no issue of that type
on that provision at any probability — cap recall however the threshold is set.
That ceiling is drawn on each panel.

Usage:
    python src/experiments/plot_issue_alignment_thresholds.py
"""
import csv
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(ROOT, "output")
FIG = os.path.join(OUT_DIR, "figures", "issue_alignment_threshold_curves.png")

PREDS = os.path.join(OUT_DIR, "risk_detect_agent_preds.csv")
ALIGN = os.path.join(OUT_DIR, "issue_alignment_check.csv")
DATASET = os.path.join(OUT_DIR, "dataset.csv")

ALIGNED = 0.5            # the alignment score above which a reason is "right"

# the risk-detection figure's palette, so the two are read as one pair
C_PRECISION = "blue"
C_RECALL = "red"
C_FLAGGED = "green"
C_LENIENT_P = "#7fa8d0"  # precision without the alignment requirement
C_LENIENT_R = "#e69a9a"  # recall without it, at (provision, type) granularity
INK2 = "#52514e"

PANELS = [("all issues", (1, 2)),
          ("risk type 1 — intrinsic", (1,)),
          ("risk type 2 — relational", (2,))]


def rows(path):
    return list(csv.DictReader(open(path, newline="", encoding="utf-8")))


def load():
    """(named issues, gold issue keys, gold (provision, type) targets).

    One item per named issue: its probability, its risk type, whether it lands
    on a construed provision at all, and which of the court's recorded defects
    the judge matched it to.
    """
    align = {r["job_id"]: r for r in rows(ALIGN)}

    gold_issues, targets = [], []
    for r in rows(DATASET):
        if r["label"] != "POSITIVE":
            continue
        for n, g in enumerate(json.loads(r["issues"]), 1):
            gold_issues.append((f"{r['contract_id']}__{r['clause_id']}__i{n}",
                                int(g["risk_type"][0])))
        targets.append((r["contract_id"], r["clause_id"]))

    items, n_clauses = [], 0
    for r in rows(PREDS):
        if r["ok"] != "1":
            continue
        n_clauses += 1
        # A job exists for every named issue on a construed provision, whatever
        # type it carries — the rule issue_alignment_check.jobs() applies — so
        # the ordinal counted here tracks the one inside the job_id.
        on_target = r["gold"] != "not_risky"
        seen = {1: 0, 2: 0}
        for it in json.loads(r["issues"]):
            if not it.get("issue"):
                continue                      # a null entry names no defect
            t = it["type"]
            a = None
            if on_target and t in (1, 2):
                seen[t] += 1
                a = align.get(f"{r['contract_id']}__{r['clause_id']}__t{t}_{seen[t]}")
            hit = (a and a.get("matched_key")
                   and float(a["alignment"]) >= ALIGNED)
            items.append({
                "prob": it["prob"], "type": t, "on_target": on_target,
                "target": (r["contract_id"], r["clause_id"]),
                "matched": a["matched_key"] if hit else None,
            })
    return items, gold_issues, targets, n_clauses


def sweep(items, n_gold, n_targets, n_clauses, thresholds):
    """(precision, recall, lenient precision, lenient recall, issues/clause).

    Recall counts DISTINCT gold issues matched, so two issues that name the same
    defect are one hit. The lenient pair drops the alignment test: its precision
    asks only whether an issue landed on a construed provision, and its recall
    counts distinct provisions reached, the finest granularity available
    without a match.

    The bottom row is issues kept per clause, not a percentage of the issues
    named: what a reviewer actually carries is the count per provision they
    open, and dividing by the issues the model happened to write hides it.
    """
    prec, rec, lprec, lrec, flag = [], [], [], [], []
    for t in thresholds:
        f = [i for i in items if i["prob"] >= t]
        found = {i["matched"] for i in f if i["matched"]}
        reached = {i["target"] for i in f if i["on_target"]}
        tp = sum(1 for i in f if i["matched"])
        lp = sum(1 for i in f if i["on_target"])
        prec.append(tp / len(f) if f else float("nan"))
        lprec.append(lp / len(f) if f else float("nan"))
        rec.append(len(found) / n_gold if n_gold else float("nan"))
        lrec.append(len(reached) / n_targets if n_targets else float("nan"))
        flag.append(len(f) / n_clauses if n_clauses else float("nan"))
    return prec, rec, lprec, lrec, flag


def main():
    for p in (PREDS, ALIGN, DATASET):
        if not os.path.exists(p):
            sys.exit(f"{p} does not exist — run the experiment and the "
                     f"alignment check first")
    items, gold_issues, targets, n_clauses = load()
    thresholds = [i / 100 for i in range(101)]

    panels = []
    for title, types in PANELS:
        sub = [i for i in items if i["type"] in types]
        n_gold = sum(1 for _k, t in gold_issues if t in types)
        n_targets = len(set(targets))
        reachable = len({i["matched"] for i in sub if i["matched"]})
        panels.append((title, n_gold, n_targets, reachable)
                      + sweep(sub, n_gold, n_targets, n_clauses, thresholds))

    fig, axes = plt.subplots(2, 3, figsize=(18, 11),
                             gridspec_kw={"height_ratios": [3, 2]})
    fig.suptitle("Issue-level detection — a hit needs the right provision "
                 "AND the right reason",
                 fontsize=19, y=0.975)
    fig.text(0.5, 0.935,
             f"{len(items):,} named issues  ·  {len(gold_issues)} defects the "
             f"courts construed  ·  a reason counts as right at "
             f"alignment ≥ {ALIGNED}",
             ha="center", fontsize=12.5, color=INK2)

    handles = [
        plt.Line2D([], [], color=C_PRECISION, ls="--", label="Precision"),
        plt.Line2D([], [], color=C_RECALL, ls="--",
                   label="Recall (of the court's defects)"),
        plt.Line2D([], [], color=C_LENIENT_P, ls=":",
                   label="Precision, alignment not required"),
        plt.Line2D([], [], color=C_LENIENT_R, ls=":",
                   label="Recall, alignment not required (per provision)"),
    ]
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 0.918),
               ncol=4, frameon=False, fontsize=12)

    for col, (title, n_gold, n_targets, reachable,
              prec, rec, lprec, lrec, flag) in enumerate(panels):
        top, bot = axes[0][col], axes[1][col]
        cap = reachable / n_gold if n_gold else 0
        top.set_title(f"{title}\n{n_gold} defects, {reachable} ever matched "
                      f"(recall capped at {cap:.0%})  ·  "
                      f"{n_targets} construed provisions",
                      fontsize=13)
        top.plot(thresholds, lprec, ls=":", color=C_LENIENT_P, lw=1.8)
        top.plot(thresholds, lrec, ls=":", color=C_LENIENT_R, lw=1.8)
        top.plot(thresholds, prec, ls="--", color=C_PRECISION, lw=1.8)
        top.plot(thresholds, rec, ls="--", color=C_RECALL, lw=1.8)
        top.axhline(cap, color=INK2, lw=0.9, ls="-", alpha=0.45)
        top.set_ylim(0, 1.02)
        top.set_xlim(0, 1)
        top.grid(alpha=0.25)
        if col == 0:
            top.set_ylabel("Precision / Recall", fontsize=12.5)

        bot.plot(thresholds, flag, ls="--", color=C_FLAGGED, lw=1.8)
        bot.set_xlim(0, 1)
        bot.set_ylim(0, max(0.05, max(flag) * 1.12))
        bot.grid(alpha=0.25)
        bot.set_xlabel("Threshold on the issue's probability", fontsize=12.5)
        if col == 0:
            bot.set_ylabel("issues kept per clause", fontsize=12.5)

    fig.tight_layout(rect=(0, 0, 1, 0.905))
    os.makedirs(os.path.dirname(FIG), exist_ok=True)
    fig.savefig(FIG, dpi=120)

    print(f"{len(items):,} named issues, {len(gold_issues)} gold defects, "
          f"{len(set(targets))} construed provisions, {n_clauses:,} clauses")
    i = thresholds.index(0.5)
    for title, n_gold, n_targets, reachable, prec, rec, lprec, lrec, flag in panels:
        print(f"  {title:<26} {n_gold:3d} defects ({reachable} matched)   @0.5  "
              f"P={prec[i]:.3f} R={rec[i]:.3f}   without alignment "
              f"P={lprec[i]:.3f} R={lrec[i]:.3f}   "
              f"{flag[i]:.3f} issue/clause")
    print(f"-> {FIG}")


if __name__ == "__main__":
    main()

"""Threshold-sweep figure at the ISSUE level, with alignment required.

The risk-detection figure scores a provision: right or wrong about whether a
court construed it. This one scores an **issue** — a named defect — and counts
it correct only when all three hold:

    the provision was construed  AND  the risk type matches  AND  the
    named defect is one the court actually construed (alignment >= 0.5)

so a provision flagged for the wrong reason is a false positive here even
though the other figure counts it as a hit. The gap between the two curves is
the price of demanding the right reason.

**The two recalls have different denominators, and the figure says so.** Strict
recall is over the court's own defects: step 2 records each one separately, and
the judge reports WHICH it matched, so a found defect can be counted once. That
denominator did not exist before — one passage per provision collapsed several
defects into one target, and what was called recall was really target coverage.
Lenient recall, which drops the alignment test, can only be measured at the old
granularity: without a match there is no way to say which defect was reached,
so it counts (provision, risk type) targets.

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
    on a gold (provision, type) target at all, and which of the court's recorded
    defects the judge matched it to.
    """
    align = {r["job_id"]: r for r in rows(ALIGN)}

    gold_issues, targets = [], []
    for r in rows(DATASET):
        if r["label"] != "POSITIVE":
            continue
        for n, g in enumerate(json.loads(r["issues"]), 1):
            gold_issues.append((f"{r['contract_id']}__{r['clause_id']}__i{n}",
                                int(g["risk_type"][0])))
        for t in sorted({int(g["risk_type"][0])
                         for g in json.loads(r["issues"])}):
            targets.append(((r["contract_id"], r["clause_id"], t), t))

    items = []
    for r in rows(PREDS):
        if r["ok"] != "1":
            continue
        gold = {1: int(r["gold_type1"]), 2: int(r["gold_type2"])}
        seen = {1: 0, 2: 0}
        for it in json.loads(r["issues"]):
            if not it.get("issue"):
                continue                      # a null entry names no defect
            t = it["type"]
            on_target = bool(gold.get(t))
            a = None
            if on_target:
                seen[t] += 1
                a = align.get(f"{r['contract_id']}__{r['clause_id']}__t{t}_{seen[t]}")
            hit = (a and a.get("matched_key")
                   and float(a["alignment"]) >= ALIGNED)
            items.append({
                "prob": it["prob"], "type": t, "on_target": on_target,
                "target": (r["contract_id"], r["clause_id"], t),
                "matched": a["matched_key"] if hit else None,
            })
    return items, gold_issues, targets


def sweep(items, n_gold, n_targets, thresholds):
    """(precision, recall, lenient precision, lenient recall, % flagged).

    Recall counts DISTINCT gold issues matched, so two issues that name the same
    defect are one hit. The lenient pair drops the alignment test: its precision
    asks only whether an issue landed on a gold (provision, type) target, and
    its recall counts distinct targets reached, which is the finest granularity
    available without a match.
    """
    prec, rec, lprec, lrec, flag = [], [], [], [], []
    n = len(items)
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
        flag.append(100.0 * len(f) / n if n else float("nan"))
    return prec, rec, lprec, lrec, flag


def main():
    for p in (PREDS, ALIGN, DATASET):
        if not os.path.exists(p):
            sys.exit(f"{p} does not exist — run the experiment and the "
                     f"alignment check first")
    items, gold_issues, targets = load()
    thresholds = [i / 100 for i in range(101)]

    panels = []
    for title, types in PANELS:
        sub = [i for i in items if i["type"] in types]
        n_gold = sum(1 for _k, t in gold_issues if t in types)
        n_targets = sum(1 for _k, t in targets if t in types)
        reachable = len({i["matched"] for i in sub if i["matched"]})
        panels.append((title, n_gold, n_targets, reachable)
                      + sweep(sub, n_gold, n_targets, thresholds))

    fig, axes = plt.subplots(2, 3, figsize=(18, 11),
                             gridspec_kw={"height_ratios": [3, 2]})
    fig.suptitle("Issue-level detection — a hit needs the right provision, the "
                 "right risk type AND the right reason",
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
                   label="Recall, alignment not required (per provision+type)"),
    ]
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 0.918),
               ncol=4, frameon=False, fontsize=12)

    for col, (title, n_gold, n_targets, reachable,
              prec, rec, lprec, lrec, flag) in enumerate(panels):
        top, bot = axes[0][col], axes[1][col]
        cap = reachable / n_gold if n_gold else 0
        top.set_title(f"{title}\n{n_gold} defects, {reachable} ever matched "
                      f"(recall capped at {cap:.0%})  ·  {n_targets} targets",
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
        bot.set_ylim(0, 102)
        bot.grid(alpha=0.25)
        bot.set_xlabel("Threshold on the issue's probability", fontsize=12.5)
        if col == 0:
            bot.set_ylabel("% of named issues flagged", fontsize=12.5)

    fig.tight_layout(rect=(0, 0, 1, 0.905))
    os.makedirs(os.path.dirname(FIG), exist_ok=True)
    fig.savefig(FIG, dpi=120)

    print(f"{len(items):,} named issues, {len(gold_issues)} gold defects, "
          f"{len(targets)} (provision, type) targets")
    i = thresholds.index(0.5)
    for title, n_gold, n_targets, reachable, prec, rec, lprec, lrec, flag in panels:
        print(f"  {title:<26} {n_gold:3d} defects ({reachable} matched)   @0.5  "
              f"P={prec[i]:.3f} R={rec[i]:.3f}   without alignment "
              f"P={lprec[i]:.3f} R={lrec[i]:.3f}   flagged {flag[i]:.1f}%")
    print(f"-> {FIG}")


if __name__ == "__main__":
    main()

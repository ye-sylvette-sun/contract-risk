"""Threshold-sweep figure at the ISSUE level, with alignment required.

The risk-detection figure scores a provision: right or wrong about whether a
court construed it. This one scores an **issue** — a named defect — and counts
it correct only when all three hold:

    the provision was construed  AND  the risk type matches  AND  the
    named defect is the one the court actually construed (alignment >= 0.5)

so a provision flagged for the wrong reason is a false positive here even
though the other figure counts it as a hit. The gap between the two curves is
the price of demanding the right reason.

**Units.** Precision is over predictions: named issues at or above the
threshold. Recall is over targets: the (provision, risk type) pairs a court
actually construed, 222 of them. Each issue that clears all three tests hits a
distinct target, so the two share a numerator without double counting.

**Null-text entries are excluded from the universe.** An entry with `issue:
null` states a probability without naming a defect, so it can never be right
for the right reason and scoring it here would only dilute the denominator. It
costs nothing: no null entry in the run exceeds 0.30.

**31 of the 222 targets are unreachable** — the model named no issue of that
type on that provision at any probability — so recall is capped at 86.0%
however the threshold is set. That ceiling is drawn on the figure.

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

ALIGNED = 0.5            # the alignment score above which a reason is "right"

# the risk-detection figure's palette, so the two are read as one pair
C_PRECISION = "blue"
C_RECALL = "red"
C_FLAGGED = "green"
C_LENIENT_P = "#7fa8d0"  # precision without the alignment requirement
C_LENIENT_R = "#e69a9a"  # recall without it
INK2 = "#52514e"

PANELS = [("all targets", (1, 2)),
          ("risk type 1 — intrinsic", (1,)),
          ("risk type 2 — relational", (2,))]


def load():
    """(named issues, number of gold targets).

    One item per named issue: its probability, its risk type, whether it lands
    on a gold (provision, type) target at all, and whether the court construed
    the defect it names.
    """
    align = {r["job_id"]: float(r["alignment"])
             for r in csv.DictReader(open(ALIGN, newline="", encoding="utf-8"))}
    items, targets = [], []
    for r in csv.DictReader(open(PREDS, newline="", encoding="utf-8")):
        if r["ok"] != "1":
            continue
        gold = {1: int(r["gold_type1"]), 2: int(r["gold_type2"])}
        targets += [t for t in (1, 2) if gold[t]]
        seen = {1: 0, 2: 0}
        for it in json.loads(r["issues"]):
            if not it.get("issue"):
                continue                      # a null entry names no defect
            t = it["type"]
            on_target = bool(gold.get(t))
            job = None
            if on_target:
                seen[t] += 1
                job = f"{r['contract_id']}__{r['clause_id']}__t{t}_{seen[t]}"
            items.append({
                "prob": it["prob"], "type": t,
                "on_target": on_target,
                "correct": on_target and align.get(job, 0.0) >= ALIGNED,
            })
    return items, targets


def sweep(items, n_targets, thresholds):
    """(precision, recall, lenient precision, lenient recall, % flagged).

    The lenient pair drops the alignment test and asks only whether the issue
    landed on a gold (provision, type) target. Both are drawn, because the two
    gaps say different things: the precision gap is how often a flagged issue
    names the wrong defect, the recall gap is how many targets were reached but
    misdescribed. A target counts once in the lenient recall even when two
    issues land on it, exactly as in the strict one.
    """
    prec, rec, lprec, lrec, flag = [], [], [], [], []
    n = len(items)
    for t in thresholds:
        f = [i for i in items if i["prob"] >= t]
        tp = sum(1 for i in f if i["correct"])
        lp = sum(1 for i in f if i["on_target"])
        prec.append(tp / len(f) if f else float("nan"))
        lprec.append(lp / len(f) if f else float("nan"))
        rec.append(tp / n_targets if n_targets else float("nan"))
        lrec.append(lp / n_targets if n_targets else float("nan"))
        flag.append(100.0 * len(f) / n)
    return prec, rec, lprec, lrec, flag


def main():
    items, targets = load()
    thresholds = [i / 100 for i in range(101)]

    panels = []
    for title, types in PANELS:
        sub = [i for i in items if i["type"] in types]
        n_t = sum(1 for t in targets if t in types)
        reachable = len({(i["type"]) for i in sub}) and sum(
            1 for i in sub if i["on_target"])
        panels.append((title, n_t, reachable, sub) + sweep(sub, n_t, thresholds))

    fig, axes = plt.subplots(2, 3, figsize=(18, 11),
                             gridspec_kw={"height_ratios": [3, 2]})
    fig.suptitle("Issue-level detection — a hit needs the right provision, the "
                 "right risk type AND the right reason",
                 fontsize=19, y=0.975)
    fig.text(0.5, 0.935,
             f"{len(items):,} named issues  ·  {len(targets)} gold "
             f"(provision, risk type) targets  ·  a reason counts as right at "
             f"alignment ≥ {ALIGNED}",
             ha="center", fontsize=12.5, color=INK2)

    handles = [
        plt.Line2D([], [], color=C_PRECISION, ls="--", label="Precision"),
        plt.Line2D([], [], color=C_RECALL, ls="--", label="Recall"),
        plt.Line2D([], [], color=C_LENIENT_P, ls=":",
                   label="Precision, alignment not required"),
        plt.Line2D([], [], color=C_LENIENT_R, ls=":",
                   label="Recall, alignment not required"),
    ]
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 0.918),
               ncol=4, frameon=False, fontsize=12.5)

    for col, (title, n_t, reachable, sub, prec, rec, lprec, lrec, flag) in \
            enumerate(panels):
        top, bot = axes[0][col], axes[1][col]
        cap = reachable / n_t if n_t else 0
        top.set_title(f"{title}\n{n_t} targets, {reachable} reachable "
                      f"(recall capped at {cap:.0%})", fontsize=13.5)
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

    print(f"{len(items):,} named issues, {len(targets)} targets")
    for title, n_t, reachable, sub, prec, rec, lprec, lrec, flag in panels:
        i = thresholds.index(0.5)
        print(f"  {title:<26} targets {n_t:3d} ({reachable} reachable)   @0.5  "
              f"P={prec[i]:.3f} R={rec[i]:.3f}   without alignment "
              f"P={lprec[i]:.3f} R={lrec[i]:.3f}   flagged {flag[i]:.1f}%")
    print(f"-> {FIG}")


if __name__ == "__main__":
    main()

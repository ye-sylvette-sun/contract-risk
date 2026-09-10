"""Threshold-sweep figure at the ISSUE level, with alignment required.

The risk-detection figure scores a provision: right or wrong about whether a
court construed it. This one scores an **issue** — a named defect — and counts
it correct when both hold:

    the provision was construed  AND  the named defect is one the court
    actually construed (alignment >= 0.5)

so a provision flagged for the wrong reason is a false positive here even
though the risk-detection figure counts it as a hit. The distance between the
two figures is the price of demanding the right reason.

**Risk type is NOT part of the test.** The alignment judge is shown neither
side's type, and a candidate is not filtered by it: the dataset's labels come
from the case's Westlaw key rather than from the passage, so gating on them
discards matches where both sides name the same defect and classify it
differently. Type agreement is measured separately, over the matched pairs.

**Recall is over the court's own defects.** Step 2 records each separately and
the judge reports WHICH one an issue matched, so a defect found twice counts
once. Dropping the alignment test would leave no way to say which defect an
issue reached, so that variant is not drawn here — scoring a provision without
regard to the reason is what the risk-detection figure already does.

**Null-text entries are excluded from the universe.** An entry with `issue:
null` states a probability without naming a defect, so it can never be right for
the right reason and scoring it here would only dilute the denominator.

Gold issues the check could never reach — the model named no issue of that type
on that provision at any probability — cap recall however the threshold is set.
Each panel's title says where that ceiling is; the recall curve reaches it at
threshold 0, so it is not drawn as a line of its own.

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
INK2 = "#52514e"

# The two threshold figures are read as a pair, so their panels have to be the
# same size on the page. `tight_layout` sizes them from whatever is left after
# the header, and the headers differ — a two-row legend in one, a one-row legend
# in the other — which silently made one figure's panels 6% shorter than the
# other's. The box is therefore fixed here and the header drawn above it, with
# the same numbers in both scripts. Changing one without the other reintroduces
# the mismatch. `bbox_inches="tight"` is likewise not used: it crops to the ink,
# so the saved canvas would again depend on the header.
PANEL_BOX = dict(left=0.075, right=0.985, bottom=0.075, top=0.78,
                 wspace=0.20, hspace=0.28)
Y_TITLE, Y_SUBTITLE, Y_LEGEND = 0.985, 0.945, 0.932

PANELS = [("all issues", (1, 2)),
          ("risk type 1 — intrinsic", (1,)),
          ("risk type 2 — relational", (2,))]


def rows(path):
    return list(csv.DictReader(open(path, newline="", encoding="utf-8")))


def load():
    """(named issues, gold issue keys, construed provisions, clauses judged).

    One item per named issue: its probability, its risk type, and which of the
    court's recorded defects the judge matched it to, if any.
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
                "prob": it["prob"], "type": t,
                "matched": a["matched_key"] if hit else None,
            })
    return items, gold_issues, targets, n_clauses


def sweep(items, n_gold, n_clauses, thresholds):
    """(precision, recall, issues per clause).

    Recall counts DISTINCT gold issues matched, so two issues that name the same
    defect are one hit.

    The bottom row is issues kept per clause, not a percentage of the issues
    named: what a reviewer actually carries is the count per provision they
    open, and dividing by the issues the model happened to write hides it.
    """
    prec, rec, flag = [], [], []
    for t in thresholds:
        f = [i for i in items if i["prob"] >= t]
        found = {i["matched"] for i in f if i["matched"]}
        tp = sum(1 for i in f if i["matched"])
        prec.append(tp / len(f) if f else float("nan"))
        rec.append(len(found) / n_gold if n_gold else float("nan"))
        flag.append(len(f) / n_clauses if n_clauses else float("nan"))
    return prec, rec, flag


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
        reachable = len({i["matched"] for i in sub if i["matched"]})
        panels.append((title, n_gold, reachable)
                      + sweep(sub, n_gold, n_clauses, thresholds))

    # Deliberately the same canvas as plot_risk_detect_thresholds.py: same
    # figsize, same equal-height rows, same tick sizes. The two figures are read
    # as a pair — clause level, then issue level — and a panel that changed
    # shape between them would suggest a difference that is not there. Only the
    # bottom row's UNIT differs, and it says so on its axis.
    plt.rcParams.update({
        "font.family": ["Segoe UI", "DejaVu Sans", "sans-serif"],
    })
    fig, axes = plt.subplots(2, 3, figsize=(11.2, 7.6), dpi=200, sharex=True)

    # One scale across the three bottom panels, so their heights compare.
    top_flag = max(max(p[-1]) for p in panels)
    step = 0.2 if top_flag > 0.6 else 0.1 if top_flag > 0.3 else 0.05
    hi = step * (int(top_flag / step) + 1)

    for col, (title, n_gold, reachable, prec, rec, flag) in enumerate(panels):
        top, bot = axes[0][col], axes[1][col]
        for ax in (top, bot):
            ax.tick_params(labelsize=9)
            ax.set_xlim(0, 1)
            ax.set_xticks([0, 0.2, 0.4, 0.6, 0.8, 1.0])
            ax.grid(color="#e8e7e3", linewidth=0.7)
            ax.set_axisbelow(True)

        top.tick_params(labelbottom=True)   # sharex hides these by default
        cap = reachable / n_gold if n_gold else 0
        top.set_title(f"{title}\n{n_gold} defects, {reachable} ever matched "
                      f"(recall capped at {cap:.0%})", fontsize=10.5, pad=10)
        top.plot(thresholds, prec, ls="--", color=C_PRECISION, lw=1.5)
        top.plot(thresholds, rec, ls="--", color=C_RECALL, lw=1.5)
        top.set_ylim(0, 1.04)
        top.set_yticks([0, 0.2, 0.4, 0.6, 0.8, 1.0])

        bot.plot(thresholds, flag, ls="--", color=C_FLAGGED, lw=1.5)
        bot.set_ylim(0, hi * 1.04)
        bot.set_yticks([round(step * i, 2)
                        for i in range(int(hi / step) + 1)])
        bot.set_xlabel("Threshold", fontsize=10)

    axes[0][0].set_ylabel("Precision / Recall", fontsize=10)
    axes[1][0].set_ylabel("Issues kept per clause", fontsize=10)

    fig.suptitle("Issue-level detection — a hit needs the right provision "
                 "AND the right reason", fontsize=14, x=0.5, y=Y_TITLE)
    fig.text(0.5, Y_SUBTITLE,
             f"{len(items):,} named issues  ·  {len(gold_issues)} defects the "
             f"courts construed  ·  right at alignment ≥ {ALIGNED}",
             ha="center", fontsize=9.5, color=INK2)
    fig.legend(handles=[
        plt.Line2D([], [], color=C_PRECISION, ls="--", lw=1.5,
                   label="Precision"),
        plt.Line2D([], [], color=C_RECALL, ls="--", lw=1.5,
                   label="Recall (of the court's defects)"),
    ], loc="upper center", bbox_to_anchor=(0.5, Y_LEGEND), ncol=2,
        frameon=False, fontsize=10, labelcolor=INK2)

    fig.subplots_adjust(**PANEL_BOX)
    os.makedirs(os.path.dirname(FIG), exist_ok=True)
    fig.savefig(FIG, facecolor="white")

    print(f"{len(items):,} named issues, {len(gold_issues)} gold defects, "
          f"{len(set(targets))} construed provisions, {n_clauses:,} clauses")
    i = thresholds.index(0.5)
    for title, n_gold, reachable, prec, rec, flag in panels:
        print(f"  {title:<26} {n_gold:3d} defects ({reachable} matched)   @0.5  "
              f"P={prec[i]:.3f} R={rec[i]:.3f}   "
              f"{flag[i]:.3f} issue/clause")
    print(f"-> {FIG}")


if __name__ == "__main__":
    main()

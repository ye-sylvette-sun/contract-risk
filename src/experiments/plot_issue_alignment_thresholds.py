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

That leaves the two type panels with a type on each side, and each curve
follows its own: **precision** is over the issues the run labelled type T —
how many of those name a defect the court construed, whatever the court's
label — and **recall** is over the defects the dataset labels type T — how
many any issue of the run found, whatever the run's label. Filtering both by
one side's label would count a type-1 issue that found a type-2 defect in
neither panel, or in the wrong one.

**Recall is over the court's own defects on the clauses the run judged.** Step
2 records each separately and the judge reports WHICH one an issue matched, so
a defect found twice counts once. A defect on a clause the run never saw — the
worked examples are held out, and a partial run covers less — is not in the
denominator: the figure scores the run on what it was shown. Dropping the alignment test would leave no way to say which defect an
issue reached, so that variant is not drawn here — scoring a provision without
regard to the reason is what the risk-detection figure already does.

Above the highest probability the model assigned, nothing is flagged and
precision is 0/0. The curve holds its last defined value there rather than
inventing one: the tail is flat because there is nothing left to score, and
the height it stops at is the precision of the last issues standing.

**Null-text entries are excluded from the universe.** An entry with `issue:
null` states a probability without naming a defect, so it can never be right for
the right reason and scoring it here would only dilute the denominator.

Gold issues the check could never reach — the model named no issue of that type
on that provision at any probability — cap recall however the threshold is set.
Each panel's title says where that ceiling is; the recall curve reaches it at
threshold 0, so it is not drawn as a line of its own.

The same figure serves any run that produced a predictions file in the agent's
format and an alignment file in the judge's: point it at them and name the run.

Usage:
    python src/experiments/plot_issue_alignment_thresholds.py
    python src/experiments/plot_issue_alignment_thresholds.py \
        --preds output/spellbook_preds.csv \
        --align output/spellbook_issue_alignment.csv \
        --fig output/figures/spellbook_issue_alignment_threshold_curves.png \
        --run "Spellbook"
"""
import argparse
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
C_PCT = "#5e3a9e"        # % of clauses flagged, on the bottom row's right axis
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

# Type 0 is an issue a run raised that is not a construction risk (Spellbook's
# review has them; the agent does not). It is never judged, so it can only
# count against precision: in the first panel, and in neither type panel.
PANELS = [("all issues", (0, 1, 2)),
          ("risk type 1 — intrinsic", (1,)),
          ("risk type 2 — relational", (2,))]


def rows(path):
    return list(csv.DictReader(open(path, newline="", encoding="utf-8")))


def load(preds_path, align_path):
    """(named issues, gold issue keys, construed provisions, clauses judged).

    One item per named issue: its probability, its risk type, and which of the
    court's recorded defects the judge matched it to, if any. Gold is counted
    only on clauses the run judged.
    """
    align = {r["job_id"]: r for r in rows(align_path)}
    preds = [r for r in rows(preds_path) if r["ok"] == "1"]
    judged = {(r["contract_id"], r["clause_id"]) for r in preds}

    gold_issues, targets = [], []
    for r in rows(DATASET):
        if r["label"] != "POSITIVE":
            continue
        if (r["contract_id"], r["clause_id"]) not in judged:
            continue
        for n, g in enumerate(json.loads(r["issues"]), 1):
            gold_issues.append((f"{r['contract_id']}__{r['clause_id']}__i{n}",
                                int(g["risk_type"][0])))
        targets.append((r["contract_id"], r["clause_id"]))

    items, n_clauses = [], 0
    for r in preds:
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
                "clause": (r["contract_id"], r["clause_id"]),
                "matched": a["matched_key"] if hit else None,
            })
    return items, gold_issues, targets, n_clauses


def sweep(sub, items, gold_of, n_gold, n_clauses, thresholds):
    """(precision, recall, issues per clause, % of clauses flagged).

    `sub` is the run's issues of the panel's type and scores precision and the
    bottom row; `items` is every issue and scores recall, over the gold defects
    `gold_of` keeps. Recall counts DISTINCT gold issues matched, so two issues
    that name the same defect are one hit. Where the threshold leaves no issue
    at all, precision is 0/0 and holds its last defined value.

    The bottom row carries two readings of the same threshold. Issues kept
    per clause is what a reviewer actually carries: the count per provision
    they open, not a percentage of the issues the model happened to write.
    The share of clauses flagged is the risk-detection figure's measure, drawn
    here on the right-hand axis so the two figures can be read against each
    other: a clause is flagged when at least one of its issues survives.
    """
    prec, rec, flag, pct = [], [], [], []
    for t in thresholds:
        f = [i for i in sub if i["prob"] >= t]
        found = {i["matched"] for i in items
                 if i["prob"] >= t and i["matched"] and gold_of(i["matched"])}
        tp = sum(1 for i in f if i["matched"])
        prec.append(tp / len(f) if f else prec[-1] if prec else float("nan"))
        rec.append(len(found) / n_gold if n_gold else float("nan"))
        flag.append(len(f) / n_clauses if n_clauses else float("nan"))
        pct.append(100 * len({i["clause"] for i in f}) / n_clauses
                   if n_clauses else float("nan"))
    return prec, rec, flag, pct


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preds", default=PREDS)
    ap.add_argument("--align", default=ALIGN)
    ap.add_argument("--fig", default=FIG)
    ap.add_argument("--run", default="Issue-level detection",
                    help="what the title calls the run")
    args = ap.parse_args()

    for p in (args.preds, args.align, DATASET):
        if not os.path.exists(p):
            sys.exit(f"{p} does not exist — run the experiment and the "
                     f"alignment check first")
    items, gold_issues, targets, n_clauses = load(args.preds, args.align)
    thresholds = [i / 100 for i in range(101)]

    gold_type = dict(gold_issues)
    panels = []
    for title, types in PANELS:
        sub = [i for i in items if i["type"] in types]
        gold_of = lambda key, types=types: gold_type.get(key) in types
        n_gold = sum(1 for _k, t in gold_issues if t in types)
        reachable = len({i["matched"] for i in items
                         if i["matched"] and gold_of(i["matched"])})
        panels.append((title, n_gold, reachable)
                      + sweep(sub, items, gold_of, n_gold, n_clauses, thresholds))

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
    top_flag = max(max(p[-2]) for p in panels)
    step = 0.2 if top_flag > 0.6 else 0.1 if top_flag > 0.3 else 0.05
    hi = step * (int(top_flag / step) + 1)

    for col, (title, n_gold, reachable, prec, rec, flag, pct) in enumerate(panels):
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
        bot.tick_params(axis="y", colors=C_FLAGGED)   # numbers in the line's colour, like the right axis
        bot.set_xlabel("Threshold", fontsize=10)

        # The share of clauses flagged, on its own axis at the right. Same
        # 0–100 scale as the risk-detection figure's bottom row.
        rax = bot.twinx()
        rax.plot(thresholds, pct, ls="--", color=C_PCT, lw=1.5)
        rax.set_ylim(0, 104)
        rax.set_yticks([0, 20, 40, 60, 80, 100])
        rax.set_yticklabels(["", "20%", "40%", "60%", "80%", "100%"])
        # Labels sit inside the frame: the panel box is shared with the
        # risk-detection figure and leaves no margin for them outside, and the
        # right half of every bottom panel is empty by t = 0.6 anyway.
        rax.tick_params(axis="y", labelsize=8.5, colors=C_PCT,
                        direction="in", pad=-4)
        for tl in rax.get_yticklabels():
            tl.set_ha("right")
        if col == 0:
            # Beside the left axis's own label, so the two units of this row
            # are named on the same panel. The gap between panels has the room
            # for it; the margin outside the panel box does not.
            rax.set_ylabel("% of clauses flagged", fontsize=10, color=C_PCT,
                           rotation=270, labelpad=4, va="bottom")

    axes[0][0].set_ylabel("Precision / Recall", fontsize=10)
    axes[1][0].set_ylabel("Issues kept per clause", fontsize=10,
                          color=C_FLAGGED)

    fig.suptitle(f"{args.run} — a hit needs the right provision "
                 "AND the right reason", fontsize=14, x=0.5, y=Y_TITLE)
    fig.text(0.5, Y_SUBTITLE,
             f"{len(items):,} named issues  ·  {len(gold_issues)} defects the "
             f"courts construed on the {n_clauses:,} clauses judged  ·  "
             f"right at alignment ≥ {ALIGNED}",
             ha="center", fontsize=9.5, color=INK2)
    fig.legend(handles=[
        plt.Line2D([], [], color=C_PRECISION, ls="--", lw=1.5,
                   label="Precision"),
        plt.Line2D([], [], color=C_RECALL, ls="--", lw=1.5,
                   label="Recall (of the court's defects)"),
    ], loc="upper center", bbox_to_anchor=(0.5, Y_LEGEND), ncol=2,
        frameon=False, fontsize=10, labelcolor=INK2)
    # The bottom row's own legend, in the gap between the rows: it describes
    # those panels, not the figure, and a reader meets it where the lines are.
    # Placed nearer the row it belongs to than the tick labels above it.
    fig.subplots_adjust(**PANEL_BOX)
    row_top = axes[1][0].get_position().y1
    row_above = axes[0][0].get_position().y0
    gap = row_top + 0.4 * (row_above - row_top)
    fig.legend(handles=[
        plt.Line2D([], [], color=C_FLAGGED, ls="--", lw=1.5,
                   label="Issues kept per clause (left axis)"),
        plt.Line2D([], [], color=C_PCT, ls="--", lw=1.5,
                   label="% of clauses flagged (right axis)"),
    ], loc="center", bbox_to_anchor=(0.5, gap), ncol=2,
        frameon=False, fontsize=10, labelcolor=INK2)

    fig.subplots_adjust(**PANEL_BOX)
    os.makedirs(os.path.dirname(args.fig), exist_ok=True)
    fig.savefig(args.fig, facecolor="white")

    print(f"{len(items):,} named issues, {len(gold_issues)} gold defects, "
          f"{len(set(targets))} construed provisions, {n_clauses:,} clauses")
    i = thresholds.index(0.5)
    for title, n_gold, reachable, prec, rec, flag, pct in panels:
        print(f"  {title:<26} {n_gold:3d} defects ({reachable} matched)   @0.5  "
              f"P={prec[i]:.3f} R={rec[i]:.3f}   "
              f"{flag[i]:.3f} issue/clause   {pct[i]:.1f}% clauses flagged")
    print(f"-> {args.fig}")


if __name__ == "__main__":
    main()

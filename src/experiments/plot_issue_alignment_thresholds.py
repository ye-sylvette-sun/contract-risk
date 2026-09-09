"""Threshold-sweep figure at the ISSUE level, with alignment required.

The risk-detection figure scores a provision: right or wrong about whether a
court construed it. This one scores an **issue** — a named defect — and counts
it correct only when the named defect is one the court actually construed
(alignment >= 0.5). On a risk-type panel the defect it matched must also be
recorded under that type, so an entry that found the court's defect and filed it
under the other type is not credited; the alignment check counts those
separately, on its `cross-type` line.

A provision flagged for the wrong reason is a false positive here even though
the clause-level figure counts it as a hit. The gap between the two is the price
of demanding the right reason.

**The flag rate is not a percentage here.** A clause can carry several issues,
so the denominators of "issues named" and "clauses read" are different units.
The bottom row reports **issues per clause** instead — the same quantity a
detector reports as detections per image. It can exceed 1.

**The two recalls have different denominators, and the figure says so.** Strict
recall is over the court's own defects: step 2 records each one separately, and
the judge reports WHICH it matched, so a found defect is counted once however
many entries name it. Lenient recall, which drops the alignment test, can only
be measured at the old granularity: without a match there is no way to say which
defect was reached, so it counts (provision, risk type) targets.

**Precision here has an unknown ceiling.** The dataset records only the defects
a court construed. An entry naming a real but unlitigated defect is scored as a
miss, and nothing in this measure tells it apart from an invention. Read the
precision curve as "how much of what the model named was litigated", not as
"how much of what the model named was true".

**Null-text entries are excluded from the universe.** An entry with `issue:
null` states a probability without naming a defect, so it can never be right for
the right reason and scoring it here would only dilute the denominator.

**Only the contracts this run covered.** The gold denominators are restricted to
them: a defect in a contract never attempted is a gap in the run, not a defect
this measure failed to find.

Gold issues the check could never reach — the model named no issue that matched
them at any probability — cap recall however the threshold is set. That ceiling
is drawn on each panel.

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
    """(named issues, gold issue types, gold (provision, type) targets).

    One item per named issue: its probability, the risk type the MODEL gave it,
    whether it lands on a gold (provision, type) target at all, which of the
    court's recorded defects the judge matched it to, and the risk type that
    defect is recorded under.
    """
    align = {r["job_id"]: r for r in rows(ALIGN)}
    preds = rows(PREDS)
    run = {r["contract_id"] for r in preds}

    gold_issues, targets = [], []
    for r in rows(DATASET):
        if r["label"] != "POSITIVE" or r["contract_id"] not in run:
            continue
        gs = json.loads(r["issues"])
        for g in gs:
            gold_issues.append(int(g["risk_type"][0]))
        for t in sorted({int(g["risk_type"][0]) for g in gs}):
            targets.append(t)

    items = []
    for r in preds:
        if r["ok"] != "1":
            continue
        gold = {1: int(r["gold_type1"]), 2: int(r["gold_type2"])}
        # `seen` must advance exactly as `issue_alignment_check.jobs()` numbers
        # its jobs -- once per entry that names a defect and carries a usable
        # type, on every provision some court construed, whatever type the entry
        # was given. Advancing it only on same-type entries (as this did while
        # the check filtered candidates by type) shifts every later entry onto
        # another entry's verdict.
        construed = r["gold"] != "not_risky"
        seen = {1: 0, 2: 0}
        for it in json.loads(r["issues"]):
            if not it.get("issue"):
                continue                      # a null entry names no defect
            t = it["type"]
            if t not in (1, 2):
                continue
            a = None
            if construed:
                seen[t] += 1
                a = align.get(f"{r['contract_id']}__{r['clause_id']}__t{t}_{seen[t]}")
            hit = (a and a.get("matched_key")
                   and float(a["alignment"]) >= ALIGNED)
            items.append({
                "prob": it["prob"], "type": t, "on_target": bool(gold.get(t)),
                "target": (r["contract_id"], r["clause_id"], t),
                "matched": a["matched_key"] if hit else None,
                "matched_type": (a.get("matched_type", "") if hit else ""),
            })
    n_clauses = len({(r["contract_id"], r["clause_id"]) for r in preds})
    return items, gold_issues, targets, n_clauses


def sweep(items, n_gold, n_targets, n_clauses, thresholds, types):
    """(precision, recall, lenient precision, lenient recall, issues/clause).

    Recall counts DISTINCT gold defects matched, so two entries naming the same
    defect are one hit. On a risk-type panel a match only counts when the
    matched defect is recorded under that type.

    The lenient pair drops the alignment test: its precision asks only whether
    an entry landed on a gold (provision, type) target, and its recall counts
    distinct targets reached, which is the finest granularity available without
    a match.
    """
    def strict(i):
        return bool(i["matched"]) and (
            len(types) == 2
            or i["matched_type"].startswith(str(types[0])))

    prec, rec, lprec, lrec, per = [], [], [], [], []
    for t in thresholds:
        f = [i for i in items if i["prob"] >= t]
        found = {i["matched"] for i in f if strict(i)}
        reached = {i["target"] for i in f if i["on_target"]}
        tp = sum(1 for i in f if strict(i))
        lp = sum(1 for i in f if i["on_target"])
        prec.append(tp / len(f) if f else float("nan"))
        lprec.append(lp / len(f) if f else float("nan"))
        rec.append(len(found) / n_gold if n_gold else float("nan"))
        lrec.append(len(reached) / n_targets if n_targets else float("nan"))
        per.append(len(f) / n_clauses if n_clauses else float("nan"))
    return prec, rec, lprec, lrec, per


def main():
    for p in (PREDS, ALIGN, DATASET):
        if not os.path.exists(p):
            sys.exit(f"{p} does not exist — run the experiment and the "
                     f"alignment check first")
    items, gold_issues, targets, n_clauses = load()
    # Zero is left out for the same reason as the clause-level figure: every
    # entry has a probability above 0, so t=0 is not an operating point.
    thresholds = [i / 100 for i in range(1, 101)]

    panels = []
    for title, types in PANELS:
        sub = [i for i in items if i["type"] in types]
        n_gold = sum(1 for t in gold_issues if t in types)
        n_targets = sum(1 for t in targets if t in types)
        got = sweep(sub, n_gold, n_targets, n_clauses, thresholds, types)
        # The ceiling: distinct gold defects this panel ever matched, at any
        # threshold. Reading it off the sweep's own first point keeps it
        # consistent with the curve rather than computed by a second rule.
        reachable = round(got[1][0] * n_gold)
        panels.append((title, n_gold, n_targets, reachable) + got)

    plt.rcParams.update({
        "font.family": ["Segoe UI", "DejaVu Sans", "sans-serif"],
    })
    fig, axes = plt.subplots(2, 3, figsize=(13.5, 8.6), dpi=160,
                             gridspec_kw={"height_ratios": [3, 2]})
    fig.suptitle("Issue-level detection — a hit needs the right provision, the "
                 "right risk type AND the right reason",
                 fontsize=14.5, y=0.975)
    fig.text(0.5, 0.932,
             f"{len(items):,} named issues  ·  {len(gold_issues)} defects the "
             f"courts construed in the 50 contracts run  ·  a reason counts as "
             f"right at alignment ≥ {ALIGNED}",
             ha="center", fontsize=10, color=INK2)

    handles = [
        plt.Line2D([], [], color=C_PRECISION, ls="--", label="Precision"),
        plt.Line2D([], [], color=C_RECALL, ls="--",
                   label="Recall (of the court's defects)"),
        plt.Line2D([], [], color=C_LENIENT_P, ls=":",
                   label="Precision, alignment not required"),
        plt.Line2D([], [], color=C_LENIENT_R, ls=":",
                   label="Recall, alignment not required (per provision+type)"),
    ]
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 0.912),
               ncol=4, frameon=False, fontsize=9.5, labelcolor=INK2)

    for col, (title, n_gold, n_targets, reachable,
              prec, rec, lprec, lrec, per) in enumerate(panels):
        top, bot = axes[0][col], axes[1][col]
        cap = reachable / n_gold if n_gold else 0
        top.set_title(f"{title}\n{n_gold} defects, {reachable} ever matched "
                      f"(recall capped at {cap:.0%})  ·  {n_targets} targets",
                      fontsize=10.5, pad=10)
        top.plot(thresholds, lprec, ls=":", color=C_LENIENT_P, lw=1.6)
        top.plot(thresholds, lrec, ls=":", color=C_LENIENT_R, lw=1.6)
        top.plot(thresholds, prec, ls="--", color=C_PRECISION, lw=1.6)
        top.plot(thresholds, rec, ls="--", color=C_RECALL, lw=1.6)
        top.axhline(cap, color=INK2, lw=0.9, alpha=0.45)
        top.set_ylim(0, 1.02)

        bot.plot(thresholds, per, ls="--", color=C_FLAGGED, lw=1.6)
        bot.set_ylim(0, max(1.02, max(per) * 1.08))
        bot.set_xlabel("Threshold on the issue's probability", fontsize=10)

        for ax in (top, bot):
            ax.set_xlim(0, 1)
            ax.tick_params(labelsize=9)
            ax.grid(color="#e8e7e3", linewidth=0.7)
            ax.set_axisbelow(True)

        if col == 0:
            top.set_ylabel("Precision / Recall", fontsize=10)
            # Not a percentage: the numerator counts issues and the denominator
            # counts clauses, and one clause can carry several issues.
            bot.set_ylabel("Issues flagged per clause", fontsize=10)

    fig.tight_layout(rect=(0, 0, 1, 0.9))
    os.makedirs(os.path.dirname(FIG), exist_ok=True)
    fig.savefig(FIG, facecolor="white", bbox_inches="tight")

    print(f"{len(items):,} named issues over {n_clauses} clauses, "
          f"{len(gold_issues)} gold defects, {len(targets)} "
          f"(provision, type) targets")
    i = thresholds.index(0.5)
    for title, n_gold, n_targets, reachable, prec, rec, lprec, lrec, per in panels:
        print(f"  {title:<26} {n_gold:3d} defects ({reachable} matched)   @0.5  "
              f"P={prec[i]:.3f} R={rec[i]:.3f}   without alignment "
              f"P={lprec[i]:.3f} R={lrec[i]:.3f}   "
              f"{per[i]:.3f} issues/clause")
    print(f"-> {FIG}")


if __name__ == "__main__":
    main()

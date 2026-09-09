"""Threshold-sweep figure for risk detection.

Three binary tasks swept over a flagging threshold t in [0, 1]: risky vs not
(score `max(prob_type1, prob_type2)`), risk type 1 vs not (`prob_type1`),
risk type 2 vs not (`prob_type2`). The two risk-type panels are ONE-VS-REST —
for risk type 1 a risk type-2 clause counts as a negative — because each
probability is an independent judgement, not a share of one distribution.

A provision's score is the strongest issue of that type it carries, so a
provision counts as flagged when the issue list holds ANY entry above the
threshold. Which entry ranked highest is not what these panels ask.

Per task: precision and recall on top, the share of clauses flagged below. The
bottom row stops the top being read too kindly — near 2% prevalence, flagging a
third of the contract can still post a respectable recall.

`--align` draws the same sweep with one thing changed: a flagged positive only
counts as a hit when one of the entries at or above the threshold names the
defect the court actually construed, as judged by `issue_alignment_check.py`.

    the SCORE is untouched            what a reader would threshold in practice
    the FLAG RATE is untouched        so the bottom row is identical to `--run`
    the HIT is redefined              flagged for the reason the court had

Precision and recall therefore fall by exactly the price of demanding the right
reason, on the same rows and the same x-axis, which is what makes the two
figures a subtraction rather than two separate experiments.

Two cautions on the aligned figure. Its precision has an unknown ceiling: the
dataset records only defects a court construed, so an entry naming a real but
unlitigated defect is scored as a miss with no way to tell it from a
hallucination. And its recall drops for two reasons at once — the model looked
at the wrong thing, or it found one of the provision's several real defects and
the court construed another — which this measure cannot separate.

Artefacts of a run, <run> being `agent`:

    risk_detect_<run>_preds.csv                 one row per provision
    risk_detect_<run>/                          the model's returned judgments
    llm_logs/risk_detect_<run>/                 request, response and usage per call
    issue_alignment_check.csv                   needed only by --align
    figures/risk_detect_<run>_threshold_curves.png

Usage:
    python src/experiments/plot_risk_detect_thresholds.py
    python src/experiments/plot_risk_detect_thresholds.py --align
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
FIG_DIR = os.path.join(OUT_DIR, "figures")
ALIGN_CSV = os.path.join(OUT_DIR, "issue_alignment_check.csv")

ALIGNED = 0.5            # the alignment score above which a reason is "right"

# The two experiments write the same columns, so one figure script serves both.
# Each entry is (predictions file, figure file, title, subtitle). The approach is
# named in the TITLE, so a reader need not work out from the filename which run
# a figure came from. A second entry is how another run gets its own figure.
RUNS = {
    "agent": ("risk_detect_agent_preds.csv", "risk_detect_agent_threshold_curves.png",
              "Agentic approach",
              "few-shot with judicial reasoning, one agent session per contract"),
}

# (panel title, how to score a row, what counts as a positive, which of the
#  provision's ALIGNED entries this panel will accept as the right reason)
#
# The last element only matters under --align. For the risky panel any matched
# defect will do; for a risk-type panel the entry must carry that type AND the
# defect it matched must be recorded under it, so an entry that found the
# court's defect and filed it under the other type is not credited here. That
# case is real and common — it is counted separately, in the alignment check's
# own `cross-type` line.
TASKS = [
    ("Risky vs not",
     lambda r: max(float(r["prob_type1"]), float(r["prob_type2"])),
     lambda r: r["gold"] != "not_risky",
     lambda al: [p for _t, p, _mt in al]),
    # One-vs-rest, and NOT exclusive: a clause gold for both risk types is a
    # positive in both panels.
    ("risk type 1 vs not — intrinsic defect",
     lambda r: float(r["prob_type1"]),
     lambda r: r.get("gold_type1") in (1, "1"),
     lambda al: [p for t, p, mt in al if t == 1 and mt.startswith("1")]),
    ("risk type 2 vs not — relational defect",
     lambda r: float(r["prob_type2"]),
     lambda r: r.get("gold_type2") in (1, "1"),
     lambda al: [p for t, p, mt in al if t == 2 and mt.startswith("2")]),
]

# classic matplotlib look: dashed primary-color lines on a plain white box
C_PRECISION = "blue"
C_RECALL = "red"
C_FLAGGED = "green"
INK2 = "#52514e"

FLAG_DEFAULT = 0.5


def load(preds):
    """Every attempted clause, deduped on (contract_id, clause_id).

    A clause the model never returned a judgment for (`ok=0`) is kept, with
    both probabilities at 0 -- unflagged at every threshold above zero. Silence
    is a prediction of "not risky", not grounds for dropping the row: a missing
    positive should cost recall, and a missing negative should still count
    toward the flag rate.
    """
    rows = {}
    with open(preds, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            key = (r["contract_id"], r["clause_id"])
            prev = rows.get(key)
            if prev is not None and (prev["ok"] == "1" or r["ok"] != "1"):
                continue                  # never let a blank mask a real score
            if r["ok"] != "1":
                r["prob_type1"] = r["prob_type2"] = "0"
            rows[key] = r
    return list(rows.values())


def load_alignment(path):
    """job_id -> (alignment, matched_key, matched risk type) from the check."""
    with open(path, newline="", encoding="utf-8") as f:
        return {r["job_id"]: (float(r["alignment"]), r["matched_key"],
                              r.get("matched_type", ""))
                for r in csv.DictReader(f)}


def aligned_entries(row, align):
    """This provision's entries that named a defect the court really construed.

    Returns [(the risk type the MODEL gave, the probability, the risk type the
    COURT's matched defect is recorded under)] -- the last of the three is what
    lets a risk-type panel refuse an entry that found the right defect under the
    wrong type.

    Job ids are rebuilt exactly as `issue_alignment_check.jobs()` numbers them:
    per provision, per model risk type, in the order the issues were returned.
    That numbering only advances on entries that name a defect and carry a
    usable type, so this loop has to skip the same ones, or every entry after a
    skipped one would join to the wrong verdict.
    """
    out, seen = [], {1: 0, 2: 0}
    if row["ok"] != "1" or row["gold"] == "not_risky":
        return out          # no recorded defect here, so nothing to align to
    for it in json.loads(row["issues"] or "[]"):
        t = it.get("type")
        if not it.get("issue") or t not in (1, 2):
            continue
        seen[t] += 1
        a = align.get(f"{row['contract_id']}__{row['clause_id']}__t{t}_{seen[t]}")
        if a and a[1] and a[0] >= ALIGNED:
            out.append((t, float(it["prob"]), a[2]))
    return out


def sweep(scored, thresholds, require_align=False):
    """(precision, recall, flagged %) per threshold; NaN where undefined.

    `scored` is (score, is positive, probabilities of this row's aligned
    entries). Without `require_align` the third element is ignored and a
    flagged positive is a hit.

    With it, a flagged positive is a hit only when one of its aligned entries is
    itself at or above the threshold. Not merely "this provision has an aligned
    entry somewhere": an entry at 0.05 must not rescue a provision that got
    flagged at 0.60 for something else, or the measure would credit the model
    for a reason it ranked far below what it actually reported.

    The flag count never consults alignment, so the flag-rate curve is identical
    with and without it, and precision and recall fall by the price of the
    stricter hit.
    """
    n = len(scored)
    n_pos = sum(1 for _, y, _ in scored if y)
    prec, rec, flag = [], [], []
    for t in thresholds:
        tp = sum(1 for s, y, al in scored
                 if y and s >= t and (not require_align
                                      or any(p >= t for p in al)))
        fl = sum(1 for s, _, _ in scored if s >= t)
        prec.append(tp / fl if fl else float("nan"))
        rec.append(tp / n_pos if n_pos else float("nan"))
        flag.append(100.0 * fl / n)
    return prec, rec, flag


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", choices=sorted(RUNS), default="agent",
                    help="which run's predictions to plot")
    ap.add_argument("--align", action="store_true",
                    help="a hit also needs the entry to name the defect the "
                         "court construed (issue_alignment_check.csv)")
    args = ap.parse_args()
    # `run_title`, not `title` — the panel loops below bind `title` to each
    # panel's own name, and a shared name would put the last panel's title on
    # the whole figure.
    preds_name, fig_name, run_title, subtitle = RUNS[args.run]
    preds = os.path.join(OUT_DIR, preds_name)

    align = {}
    if args.align:
        if not os.path.exists(ALIGN_CSV):
            sys.exit(f"{ALIGN_CSV} does not exist — run "
                     f"src/experiments/issue_alignment_check.py first")
        align = load_alignment(ALIGN_CSV)
        fig_name = fig_name.replace(".png", "_aligned.png")
        run_title += " — the right provision AND the right reason"

    rows = load(preds)
    n_contracts = len({r["contract_id"] for r in rows})
    print(f"{len(rows)} clauses scored over {n_contracts} contracts"
          f"{'  (alignment required)' if args.align else ''}")
    blank = [r for r in rows if r["ok"] != "1"]
    if blank:
        n_pos = sum(1 for r in blank if r["gold"] != "not_risky")
        print(f"  ! {len(blank)} returned no judgment ({n_pos} positive) "
              f"-- counted as not flagged")

    by_row = {id(r): aligned_entries(r, align) for r in rows} if align else {}
    if args.align:
        n_al = sum(1 for r in rows if by_row[id(r)])
        print(f"  {n_al} provision(s) carry at least one entry the judge "
              f"matched to a recorded defect at alignment >= {ALIGNED}")

    # 0.01, 0.02, ..., 1.00 — evaluated at every 0.01; consecutive points are
    # joined by line segments, which softens the staircase a little.
    #
    # Zero is left out on purpose. `s >= 0` is true of a provision the model
    # scored 0, so t=0 flags the whole contract and posts recall 1.0 — a point
    # no reader would ever operate at, and one that draws a vertical drop into
    # the curve at the left edge that has nothing to do with the model's
    # ranking. Where the ceiling matters it is the t=0.01 value, which is what
    # this now starts from: the recall available once silence is taken as a
    # prediction of "not risky".
    thresholds = [i / 100 for i in range(1, 101)]
    panels = []
    for title, score_of, is_pos, aligned_of in TASKS:
        scored = [(score_of(r), is_pos(r),
                   aligned_of(by_row[id(r)]) if args.align else ())
                  for r in rows]
        n_pos = sum(1 for _, y, _ in scored if y)
        prec, rec, flag = sweep(scored, thresholds, args.align)
        # The recall ceiling: positives with an aligned entry at all. No
        # threshold can reach past it, so the panel says where it is instead of
        # letting a curve that flattens early look like a scoring artefact.
        cap = (sum(1 for _, y, al in scored if y and al) / n_pos
               if args.align and n_pos else None)
        panels.append((title, n_pos, prec, rec, flag, cap))

        tp = sum(1 for s, y, al in scored
                 if y and s >= FLAG_DEFAULT
                 and (not args.align or any(p >= FLAG_DEFAULT for p in al)))
        fl = sum(1 for s, _, _ in scored if s >= FLAG_DEFAULT)
        p = f"{tp / fl:.2f}" if fl else "n/a"
        r_ = f"{tp / n_pos:.2f}" if n_pos else "n/a"
        capped = f"  ceiling {cap:.0%}" if cap is not None else ""
        print(f"  {title:36} {n_pos:3d} positive  @{FLAG_DEFAULT}  "
              f"P={p}  R={r_}  ({fl} flagged = {100 * fl / len(rows):.0f}%)"
              f"{capped}")

    plt.rcParams.update({
        "font.family": ["Segoe UI", "DejaVu Sans", "sans-serif"],
    })
    fig, axes = plt.subplots(2, 3, figsize=(11.2, 7.6), dpi=200, sharex=True)

    for col, (title, n_pos, prec, rec, flag, cap) in enumerate(panels):
        top, bot = axes[0][col], axes[1][col]
        for ax in (top, bot):
            ax.tick_params(labelsize=9)
            ax.set_xlim(0, 1)
            ax.set_xticks([0, 0.2, 0.4, 0.6, 0.8, 1.0])
            ax.grid(color="#e8e7e3", linewidth=0.7)
            ax.set_axisbelow(True)

        top.tick_params(labelbottom=True)   # sharex hides these by default
        head = (f"{title}\n{n_pos} positive of {len(rows)} "
                f"({100 * n_pos / len(rows):.1f}%)")
        if cap is not None:
            head += f"  ·  recall capped at {cap:.0%}"
        top.set_title(head, fontsize=10.5, pad=10)
        top.plot(thresholds, prec, color=C_PRECISION, linestyle="--",
                 linewidth=1.5, label="Precision")
        top.plot(thresholds, rec, color=C_RECALL, linestyle="--",
                 linewidth=1.5, label="Recall")
        if cap is not None:
            top.axhline(cap, color=INK2, linewidth=0.9, alpha=0.45)
        top.set_ylim(0, 1.04)
        top.set_yticks([0, 0.2, 0.4, 0.6, 0.8, 1.0])

        bot.plot(thresholds, flag, color=C_FLAGGED, linestyle="--",
                 linewidth=1.5)
        bot.set_ylim(0, 104)
        bot.set_yticks([0, 20, 40, 60, 80, 100])
        bot.set_xlabel("Threshold", fontsize=10)

    axes[0][0].set_ylabel("Precision / Recall", fontsize=10)
    axes[1][0].set_ylabel("% of clauses flagged", fontsize=10)

    fig.suptitle(f"{run_title} — precision, recall, and flag rate across "
                 f"risk-flagging thresholds", fontsize=13.5, x=0.5, y=0.985)
    # No "Exp 3" here: the experiments were renamed off those indices, and a
    # figure is the last place a stale one should survive.
    tail = (f"a hit needs an entry at or above the threshold that names the "
            f"court's own defect (alignment ≥ {ALIGNED})" if args.align
            else "the two risk-type panels are one-vs-rest")
    fig.text(0.5, 0.935,
             f"{subtitle}  ·  "
             f"{len(rows)} clauses from {n_contracts} contracts  ·  {tail}",
             ha="center", fontsize=9.5, color=INK2)
    fig.legend(handles=[
        plt.Line2D([], [], color=C_PRECISION, linestyle="--", linewidth=1.5,
                   label="Precision"),
        plt.Line2D([], [], color=C_RECALL, linestyle="--", linewidth=1.5,
                   label="Recall"),
    ], loc="upper center", bbox_to_anchor=(0.5, 0.925), ncol=2, frameon=False,
        fontsize=10, labelcolor=INK2)

    fig.tight_layout(rect=(0, 0, 1, 0.925))
    os.makedirs(FIG_DIR, exist_ok=True)
    out = os.path.join(FIG_DIR, fig_name)
    fig.savefig(out, facecolor="white", bbox_inches="tight")
    print(f"-> {out}")


if __name__ == "__main__":
    main()

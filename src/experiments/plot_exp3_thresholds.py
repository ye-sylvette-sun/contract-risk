"""Threshold-sweep figure for experiment 3.

Three binary tasks, side by side, each swept over a flagging threshold t in
[0, 1]:

  * **risky vs not** — score `max(prob_cat1, prob_cat2)`, positive when the court
    construed the clause at all;
  * **type 1 vs not** — score `prob_cat1`, positive when the construction turned
    on an intrinsic textual defect (taxonomy 1.x);
  * **type 2 vs not** — score `prob_cat2`, positive when it turned on the
    clause's relationship to the rest of the instrument (2.x).

The two type panels are **one-vs-rest**: for type 1, a type-2 clause counts as a
negative, and the other way round. That is the question the two probabilities
are actually asked — each is an independent judgement about its own category,
not a share of one distribution.

Per task:

  * top    — precision and recall against threshold
  * bottom — the share of clauses flagged against threshold

The bottom row is what stops the top one being read too kindly: at a prevalence
near 2%, a threshold that flags a third of the contract can still post a
respectable recall.

Input : output/exp3_llm_api_preds.csv
Output: output/figures/exp3_threshold_curves.png

Usage:
    python src/experiments/plot_exp3_thresholds.py
"""
import csv
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(ROOT, "output")
FIG_DIR = os.path.join(OUT_DIR, "figures")
PREDS = os.path.join(OUT_DIR, "exp3_llm_api_preds.csv")

# (panel title, how to score a row, what counts as a positive)
TASKS = [
    ("Risky vs not",
     lambda r: max(float(r["prob_cat1"]), float(r["prob_cat2"])),
     lambda r: r["gold"] != "not_risky"),
    ("Type 1 vs not — intrinsic defect",
     lambda r: float(r["prob_cat1"]),
     lambda r: r["gold"] == "risky_cat1"),
    ("Type 2 vs not — relational defect",
     lambda r: float(r["prob_cat2"]),
     lambda r: r["gold"] == "risky_cat2"),
]

# classic matplotlib look: dashed primary-color lines on a plain white box
C_PRECISION = "blue"
C_RECALL = "red"
C_FLAGGED = "green"
INK2 = "#52514e"

FLAG_DEFAULT = 0.5


def load():
    """Every attempted clause, deduped on (contract_id, clause_id).

    A clause the model never returned a judgment for (`ok=0`) is kept, with
    both probabilities at 0 -- unflagged at every threshold above zero. Silence
    is a prediction of "not risky", not grounds for dropping the row: a missing
    positive should cost recall, and a missing negative should still count
    toward the flag rate.
    """
    rows = {}
    with open(PREDS, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            key = (r["contract_id"], r["clause_id"])
            prev = rows.get(key)
            if prev is not None and (prev["ok"] == "1" or r["ok"] != "1"):
                continue                  # never let a blank mask a real score
            if r["ok"] != "1":
                r["prob_cat1"] = r["prob_cat2"] = "0"
            rows[key] = r
    return list(rows.values())


def sweep(scored, thresholds):
    """(precision, recall, flagged %) per threshold; NaN where undefined."""
    n = len(scored)
    n_pos = sum(1 for _, y in scored if y)
    prec, rec, flag = [], [], []
    for t in thresholds:
        tp = sum(1 for s, y in scored if y and s >= t)
        fl = sum(1 for s, _ in scored if s >= t)
        prec.append(tp / fl if fl else float("nan"))
        rec.append(tp / n_pos if n_pos else float("nan"))
        flag.append(100.0 * fl / n)
    return prec, rec, flag


def main():
    rows = load()
    n_contracts = len({r["contract_id"] for r in rows})
    print(f"{len(rows)} clauses scored over {n_contracts} contracts")
    blank = [r for r in rows if r["ok"] != "1"]
    if blank:
        n_pos = sum(1 for r in blank if r["gold"] != "not_risky")
        print(f"  ! {len(blank)} returned no judgment ({n_pos} positive) "
              f"-- counted as not flagged")

    # 0.00, 0.01, ..., 1.00 — evaluated at every 0.01; consecutive points are
    # joined by line segments, which softens the staircase a little
    thresholds = [i / 100 for i in range(101)]
    panels = []
    for title, score_of, is_pos in TASKS:
        scored = [(score_of(r), is_pos(r)) for r in rows]
        n_pos = sum(1 for _, y in scored if y)
        prec, rec, flag = sweep(scored, thresholds)
        panels.append((title, n_pos, prec, rec, flag))

        tp = sum(1 for s, y in scored if y and s >= FLAG_DEFAULT)
        fl = sum(1 for s, _ in scored if s >= FLAG_DEFAULT)
        p = f"{tp / fl:.2f}" if fl else "n/a"
        r_ = f"{tp / n_pos:.2f}" if n_pos else "n/a"
        print(f"  {title:36} {n_pos:3d} positive  @{FLAG_DEFAULT}  "
              f"P={p}  R={r_}  ({fl} flagged = {100 * fl / len(rows):.0f}%)")

    plt.rcParams.update({
        "font.family": ["Segoe UI", "DejaVu Sans", "sans-serif"],
    })
    fig, axes = plt.subplots(2, 3, figsize=(11.2, 7.6), dpi=200, sharex=True)

    for col, (title, n_pos, prec, rec, flag) in enumerate(panels):
        top, bot = axes[0][col], axes[1][col]
        for ax in (top, bot):
            ax.tick_params(labelsize=9)
            ax.set_xlim(0, 1)
            ax.set_xticks([0, 0.2, 0.4, 0.6, 0.8, 1.0])
            ax.grid(color="#e8e7e3", linewidth=0.7)
            ax.set_axisbelow(True)

        top.tick_params(labelbottom=True)   # sharex hides these by default
        top.set_title(f"{title}\n{n_pos} positive of {len(rows)} "
                      f"({100 * n_pos / len(rows):.1f}%)", fontsize=10.5, pad=10)
        top.plot(thresholds, prec, color=C_PRECISION, linestyle="--",
                 linewidth=1.5, label="Precision")
        top.plot(thresholds, rec, color=C_RECALL, linestyle="--",
                 linewidth=1.5, label="Recall")
        top.set_ylim(0, 1.04)
        top.set_yticks([0, 0.2, 0.4, 0.6, 0.8, 1.0])

        bot.plot(thresholds, flag, color=C_FLAGGED, linestyle="--",
                 linewidth=1.5)
        bot.set_ylim(0, 104)
        bot.set_yticks([0, 20, 40, 60, 80, 100])
        bot.set_xlabel("Threshold", fontsize=10)

    axes[0][0].set_ylabel("Precision / Recall", fontsize=10)
    axes[1][0].set_ylabel("% of clauses flagged", fontsize=10)

    fig.suptitle("Precision, recall, and flag rate across risk-flagging thresholds",
                 fontsize=14, x=0.5, y=0.985)
    fig.text(0.5, 0.935,
             f"Exp 3 — few-shot with judicial reasoning, one call per contract  ·  "
             f"{len(rows)} clauses from {n_contracts} contracts  ·  "
             f"the two type panels are one-vs-rest",
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
    out = os.path.join(FIG_DIR, "exp3_threshold_curves.png")
    fig.savefig(out, facecolor="white", bbox_inches="tight")
    print(f"-> {out}")


if __name__ == "__main__":
    main()

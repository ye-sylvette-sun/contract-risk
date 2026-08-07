"""Threshold-sweep figures for experiment 3 and its no-contract ablation.

Sweep the flagging threshold t over [0,1] and plot, for each of the three
reported channels:

  top row     precision and recall vs threshold
  bottom row  % of provisions flagged vs threshold

The original figure put one experiment per column; here the columns are the
three channels instead — category 1 ranked by prob_cat1, category 2 by
prob_cat2, and risky-vs-not by max(prob_cat1, prob_cat2), each against its own
gold class. One figure per run, so the two are read side by side.

The sweep is the point. A single operating point at 0.5 says precision and
recall are both 1.00 on the agent's binary channel; the curve shows how much of
that is headroom and how much is luck, because it shows where the number breaks.

Input : output/exp3_preds.csv, output/exp3_api_preds.csv
Output: output/figures/exp3_thresholds.png
        output/figures/exp3_api_thresholds.png

Usage:
    conda run -n legal-llm python src/experiments/plot_thresholds.py
    conda run -n legal-llm python src/experiments/plot_thresholds.py --run api
"""
import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib
from exp3_common import FLAG, load_preds

FIGS = lib.OUT / "figures"

# classic matplotlib look: dashed primary-color lines on a plain white box
C_PRECISION = "blue"
C_RECALL = "red"
C_FLAGGED = "green"
INK2 = "#52514e"

CHANNELS = [
    ("Category 1", lambda r: float(r["prob_cat1"]),
     lambda r: r["gold"] == "risky_cat1"),
    ("Category 2", lambda r: float(r["prob_cat2"]),
     lambda r: r["gold"] == "risky_cat2"),
    ("Risky vs not", lambda r: max(float(r["prob_cat1"]), float(r["prob_cat2"])),
     lambda r: r["gold"] != "not_risky"),
]

RUNS = {
    "agent": {
        "preds": "exp3_preds.csv",
        "out": "exp3_thresholds.png",
        "title": "Experiment 3 — precision, recall and flag rate across thresholds",
        "setting": "one agent session per contract, contract text in the workspace",
    },
    "api": {
        "preds": "exp3_api_preds.csv",
        "out": "exp3_api_thresholds.png",
        "title": "Experiment 3 (API) — precision, recall and flag rate across thresholds",
        "setting": "one stateless API call per contract, no contract text in the input",
    },
}


def sweep(scored, thresholds):
    """scored: [(score, is_positive)] -> precision, recall, flagged% per t.

    Precision is nan where nothing is flagged, so the curve simply stops rather
    than dropping to a fabricated zero.
    """
    n = len(scored)
    n_pos = sum(1 for _, y in scored if y)
    prec, rec, flag = [], [], []
    for t in thresholds:
        fl = sum(1 for s, _ in scored if s >= t)
        tp = sum(1 for s, y in scored if y and s >= t)
        prec.append(tp / fl if fl else float("nan"))
        rec.append(tp / n_pos if n_pos else float("nan"))
        flag.append(100.0 * fl / n)
    return prec, rec, flag


def figure(run):
    cfg = RUNS[run]
    rows = load_preds(lib.OUT / cfg["preds"])
    if not rows:
        print(f"  ({run}: no predictions in {cfg['preds']}, skipped)")
        return
    thresholds = [i / 100 for i in range(101)]

    plt.rcParams.update({"font.family": ["Segoe UI", "DejaVu Sans", "sans-serif"]})
    fig, axes = plt.subplots(2, 3, figsize=(11.2, 7.6), dpi=200, sharex=True)

    print(f"\n{run}:")
    for col, (title, score_of, is_pos) in enumerate(CHANNELS):
        scored = [(score_of(r), is_pos(r)) for r in rows]
        n_pos = sum(1 for _, y in scored if y)
        prec, rec, flag = sweep(scored, thresholds)

        top, bot = axes[0][col], axes[1][col]
        for ax in (top, bot):
            ax.tick_params(labelsize=9)
            ax.set_xlim(0, 1)
            ax.set_xticks([0, 0.2, 0.4, 0.6, 0.8, 1.0])
            ax.grid(color="#e8e7e3", linewidth=0.7)
            ax.set_axisbelow(True)
            ax.axvline(FLAG, color=INK2, linestyle=":", linewidth=1)

        top.tick_params(labelbottom=True)      # sharex hides these by default
        top.set_title(f"{title}\n{n_pos} risky of {len(rows)}", fontsize=11,
                      pad=10, linespacing=1.6)
        top.plot(thresholds, prec, color=C_PRECISION, linestyle="--",
                 linewidth=1.5)
        top.plot(thresholds, rec, color=C_RECALL, linestyle="--", linewidth=1.5)
        top.set_ylim(0, 1.06)
        top.set_yticks([0, 0.2, 0.4, 0.6, 0.8, 1.0])

        bot.plot(thresholds, flag, color=C_FLAGGED, linestyle="--", linewidth=1.5)
        bot.set_ylim(0, 106)
        bot.set_yticks([0, 20, 40, 60, 80, 100])
        bot.set_xlabel("Threshold", fontsize=10)

        # the 0.5 operating point, printed as a sanity check on the metrics
        i = thresholds.index(FLAG)
        print(f"  {title:13} @{FLAG}  P={prec[i]:.2f}  R={rec[i]:.2f}  "
              f"flagged {flag[i]:.0f}%  ({n_pos} risky)")

    axes[0][0].set_ylabel("Precision / Recall", fontsize=10)
    axes[1][0].set_ylabel("% of provisions flagged", fontsize=10)

    fig.suptitle(cfg["title"], fontsize=14, x=0.5, y=0.985)
    fig.text(0.5, 0.935,
             f"{cfg['setting']}  ·  {len(rows)} provisions from "
             f"{len({r['contract_id'] for r in rows})} contracts  ·  "
             f"claude-opus-5, high effort  ·  dotted line = the 0.5 operating point",
             ha="center", fontsize=9.5, color=INK2)
    fig.legend(handles=[
        plt.Line2D([], [], color=C_PRECISION, linestyle="--", linewidth=1.5,
                   label="Precision"),
        plt.Line2D([], [], color=C_RECALL, linestyle="--", linewidth=1.5,
                   label="Recall"),
        plt.Line2D([], [], color=C_FLAGGED, linestyle="--", linewidth=1.5,
                   label="% flagged"),
    ], loc="upper center", bbox_to_anchor=(0.5, 0.925), ncol=3, frameon=False,
        fontsize=10, labelcolor=INK2)

    fig.tight_layout(rect=(0, 0, 1, 0.91))
    FIGS.mkdir(parents=True, exist_ok=True)
    out = FIGS / cfg["out"]
    fig.savefig(out, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    print(f"  -> {out.relative_to(lib.ROOT)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", choices=[*RUNS, "both"], default="both")
    args = ap.parse_args()
    for run in (RUNS if args.run == "both" else [args.run]):
        figure(run)


if __name__ == "__main__":
    main()

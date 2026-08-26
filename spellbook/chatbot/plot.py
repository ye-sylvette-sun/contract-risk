"""Threshold-sweep figure: Spellbook against the agentic approach.

Three binary tasks swept over a flagging threshold t in [0, 1] -- risky vs not
(score `max(prob_cat1, prob_cat2)`), category 1 vs not (`prob_cat1`), category 2
vs not (`prob_cat2`). The two category panels are ONE-VS-REST, matching
src/experiments/plot_exp3_thresholds.py, because each probability is an
independent judgement rather than a share of one distribution.

Precision and recall on top, the share of provisions flagged underneath. The
bottom row is what stops the top being read too kindly: at this prevalence a
threshold that flags a fifth of the contract can post a respectable recall and
still be useless to a reviewer.

Both arms are drawn on the SAME contracts -- whichever ones have Spellbook
results -- so the comparison is like-for-like. The one-shot `llm_api` arm is
deliberately not drawn.

Usage:
    python spellbook/plot.py
"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "spellbook"))
import score  # noqa: E402

FIG = ROOT / "output" / "figures" / "spellbook_vs_agent_threshold_curves.png"

PANELS = [("Risky vs not", "risky"),
          ("Category 1 vs not — intrinsic defect", "cat1"),
          ("Category 2 vs not — relational defect", "cat2")]

# Spellbook solid, the agent dashed: the measure is the colour, the arm is the
# line style, so a reader compares like with like down a column.
C_PRECISION, C_RECALL, C_FLAGGED = "blue", "red", "green"
INK2 = "#52514e"
ARMS = [("Spellbook", "-"), ("Agentic approach", "--")]


def sweep(scores, labels, thresholds):
    """(precision, recall, flagged %) at each threshold; NaN where undefined."""
    n, n_pos = len(scores), sum(labels)
    prec, rec, flag = [], [], []
    for t in thresholds:
        tp = sum(1 for s, y in zip(scores, labels) if y and s >= t)
        fl = sum(1 for s in scores if s >= t)
        prec.append(tp / fl if fl else float("nan"))
        rec.append(tp / n_pos if n_pos else float("nan"))
        flag.append(100.0 * fl / n)
    return prec, rec, flag


def main():
    sb, cids, missing, _lines, n_all, _bad = score.collect()
    agent = score.arm_rows("exp3_agent_preds.csv", cids)
    n = len(sb)
    print(f"{len(cids)} of {n_all} contracts, {n} provisions")
    if missing:
        print(f"  ! {missing} unjudged by Spellbook, counted as not flagged")

    # each arm's operating point, chosen once on risky-vs-not and reused in
    # every panel, exactly as score.report() does it
    op = {}
    for rows in (sb, agent):
        sc, lb = score.panel(rows, "risky")
        op[id(rows)] = score.threshold_at_recall(sc, lb)
    print(f"  {score.TARGET:.0%}-recall thresholds — "
          f"Spellbook {op[id(sb)]}, agent {op[id(agent)]}")

    thresholds = [i / 100 for i in range(101)]
    plt.rcParams.update({"font.family": ["Segoe UI", "DejaVu Sans", "sans-serif"]})
    fig, axes = plt.subplots(2, 3, figsize=(11.2, 7.6), dpi=200, sharex=True)

    for col, (title, which) in enumerate(PANELS):
        top, bot = axes[0][col], axes[1][col]
        for ax in (top, bot):
            ax.tick_params(labelsize=9)
            ax.set_xlim(0, 1)
            ax.set_xticks([0, 0.2, 0.4, 0.6, 0.8, 1.0])
            ax.grid(color="#e8e7e3", linewidth=0.7)
            ax.set_axisbelow(True)
        top.tick_params(labelbottom=True)

        n_pos = None
        curves = {}
        for rows, (_arm, style) in zip((sb, agent), ARMS):
            sc, lb = score.panel(rows, which)
            n_pos = sum(lb)
            prec, rec, flag = sweep(sc, lb, thresholds)
            curves[id(rows)] = (prec, rec, flag)
            top.plot(thresholds, prec, color=C_PRECISION, linestyle=style,
                     linewidth=1.5)
            top.plot(thresholds, rec, color=C_RECALL, linestyle=style,
                     linewidth=1.5)
            bot.plot(thresholds, flag, color=C_FLAGGED, linestyle=style,
                     linewidth=1.5)

        # Each arm's own operating point: the highest threshold still reaching
        # TARGET recall on risky-vs-not. Chosen on that panel and carried across
        # all three, so a dot marks where it meets each curve in every column --
        # those dots ARE the reported numbers. The threshold itself is named in
        # each subplot's legend rather than drawn as a rule, which would cross
        # the curves at exactly the point being read.
        marks = []
        for rows, (arm, style) in zip((sb, agent), ARMS):
            t = op[id(rows)]
            if t is None:
                continue
            prec, rec, flag = curves[id(rows)]
            i = int(round(t * 100))          # thresholds are the 0.01 grid
            for ax, value, colour in ((top, prec[i], C_PRECISION),
                                      (top, rec[i], C_RECALL),
                                      (bot, flag[i], C_FLAGGED)):
                ax.plot([t], [value], marker="o", markersize=6.5,
                        color=colour, markeredgecolor="white",
                        markeredgewidth=0.8, zorder=5, clip_on=False)
            marks.append((arm, style, t))

        # Only on the flag-rate row. The thresholds are the same in both rows,
        # and the top row has no free corner -- a box there covers the precision
        # curve's right-hand climb, which is the part worth seeing.
        bot.legend(handles=[plt.Line2D([], [], color=INK2, linestyle=st,
                                       linewidth=1.4,
                                       label=f"{arm}  t = {t:.2f}")
                            for arm, st, t in marks],
                   loc="upper right", fontsize=9, labelcolor=INK2,
                   framealpha=0.92, edgecolor="#d9d8d4", borderpad=0.5,
                   handlelength=1.8, labelspacing=0.35).set_zorder(6)

        top.set_title(f"{title}\n{n_pos} positive of {n} "
                      f"({100 * n_pos / n:.1f}%)", fontsize=10.5, pad=10)
        top.set_ylim(0, 1.04)
        top.set_yticks([0, 0.2, 0.4, 0.6, 0.8, 1.0])
        bot.set_ylim(0, 104)
        bot.set_yticks([0, 20, 40, 60, 80, 100])
        bot.set_xlabel("Threshold", fontsize=10)

    axes[0][0].set_ylabel("Precision / Recall", fontsize=10)
    axes[1][0].set_ylabel("% of provisions flagged", fontsize=10)

    fig.suptitle("Spellbook vs the agentic approach — precision, recall and "
                 "flag rate across risk-flagging thresholds",
                 fontsize=14, x=0.5, y=0.985)
    fig.text(0.5, 0.945,
             f"{n} provisions from {len(cids)} contracts, judged by both  ·  "
             f"dots mark each arm's threshold for "
             f"{score.TARGET:.0%} recall on risky-vs-not  ·  "
             f"the two category panels are one-vs-rest",
             ha="center", fontsize=9.5, color=INK2)

    # Colour only. Which arm is solid and which dashed is already said by each
    # subplot's own legend, beside the threshold it applies at, so repeating it
    # here would make the reader check two keys for one fact.
    line = plt.Line2D
    fig.legend(handles=[
        line([], [], color=C_PRECISION, linewidth=1.5, label="Precision"),
        line([], [], color=C_RECALL, linewidth=1.5, label="Recall"),
        line([], [], color=C_FLAGGED, linewidth=1.5, label="% flagged"),
    ], loc="upper center", bbox_to_anchor=(0.5, 0.935), ncol=3, frameon=False,
        fontsize=10, labelcolor=INK2)

    fig.tight_layout(rect=(0, 0, 1, 0.925))
    FIG.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG, facecolor="white", bbox_inches="tight")
    print(f"-> {FIG.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

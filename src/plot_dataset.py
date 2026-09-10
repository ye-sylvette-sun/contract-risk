"""The dataset's shape: how many clauses a contract has.

The corpus is heavily right-skewed — the largest contract has 86x the clauses of
the smallest — and that matters for reading every other figure: any metric
averaged over clauses is weighted by contract size, so a handful of very long
instruments decide it.

The axis is logarithmic because a linear one shows a spike against a flat tail
and nothing else.

Reads only `output/dataset.csv`, so it needs no experiment output.

Usage:
    python src/plot_dataset.py
"""
import collections
import csv
import os
import sys

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker  # noqa: E402

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "output")
DATASET = os.path.join(OUT_DIR, "dataset.csv")
FIG = os.path.join(OUT_DIR, "figures", "clause_count_distribution.png")

INK, INK2, GRID = "#1f1e1c", "#52514e", "#e8e7e3"
C_MAIN = "#2a78d6"   # one series, one hue
C_MARK = "#b4553f"   # the median rule


def sizes():
    """Clauses per contract, ascending."""
    n = collections.Counter(
        r["contract_id"]
        for r in csv.DictReader(open(DATASET, newline="", encoding="utf-8")))
    return np.array(sorted(n.values()))


def figure(v):
    fig, ax = plt.subplots(figsize=(7.4, 4.6), dpi=200)

    ax.hist(v, bins=np.logspace(np.log10(8), np.log10(1024), 19),
            color=C_MAIN, edgecolor="white", linewidth=1.0)
    ax.set_xscale("log")
    ax.set_xticks([10, 25, 50, 100, 200, 400, 800])
    ax.get_xaxis().set_major_formatter(matplotlib.ticker.ScalarFormatter())
    ax.axvline(np.median(v), color=C_MARK, lw=1.6, ls="--")
    ax.annotate(f"median {int(np.median(v))}",
                xy=(np.median(v), ax.get_ylim()[1] * 0.96),
                xytext=(-7, 0), textcoords="offset points",
                ha="right", va="top", color=C_MARK, fontsize=9.5)
    ax.set_xlabel("Clauses in the contract (log scale)", fontsize=10.5)
    ax.set_ylabel("Contracts", fontsize=10.5)

    ax.grid(color=GRID, linewidth=0.7)
    ax.set_axisbelow(True)
    ax.tick_params(labelsize=9.5, colors=INK2)
    for s in ax.spines.values():
        s.set_color(GRID)

    ax.set_title(f"Clause count per contract — {len(v)} contracts, "
                 f"{v.sum():,} clauses", fontsize=12.5, color=INK, pad=24)
    ax.text(0.5, 1.035, f"mean {v.mean():.0f}  ·  median {int(np.median(v))}"
            f"  ·  range {v.min()}–{v.max()}", transform=ax.transAxes,
            ha="center", fontsize=9.5, color=INK2)

    fig.subplots_adjust(left=0.10, right=0.98, bottom=0.125, top=0.845)
    fig.savefig(FIG, facecolor="white")
    plt.close(fig)


def main():
    if not os.path.exists(DATASET):
        sys.exit(f"{DATASET} does not exist — build the dataset first")
    plt.rcParams.update({"font.family": ["Segoe UI", "DejaVu Sans",
                                         "sans-serif"]})
    v = sizes()
    os.makedirs(os.path.dirname(FIG), exist_ok=True)
    figure(v)
    print(f"{len(v)} contracts, {v.sum():,} clauses   "
          f"mean {v.mean():.1f}  median {int(np.median(v))}  "
          f"range {v.min()}–{v.max()}")
    print(f"-> {FIG}")


if __name__ == "__main__":
    main()

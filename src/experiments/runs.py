"""What a risk-detection run reads and what it writes.

The dataset rows an experiment is given, the `preds.csv` row it produces, and
the scoring both sides agree on. Shared, so the run and everything that reads
its output cannot disagree about what a column means.

There was a second arm — a one-shot LLM API call per contract — that this file
was carved out of when that arm was retired. Only the agent run remains; the
history is on `2026.9.3_legacy_multi_issue_experiment`.

Imported by:
    risk_detect_agent.py      the run
    compare_risk_detect.py    scoring one or two runs
"""
import csv
import json
import os
import random
import sys
from collections import OrderedDict, defaultdict

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib  # noqa: E402
import predictions  # noqa: E402

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

# The probability at which `pred` is called risky. A reporting default, not a
# property of the run: every figure sweeps the threshold, and this only fixes
# what the `pred` column says.
FLAG = 0.5

# `gold_subtype` is ground truth and free to carry; there is no predicted
# subtype — only the two risk types are asked for.
#
# `gold_type1` / `gold_type2` are separate columns rather than one label because
# a positive can be BOTH: a case filed under several Westlaw keys can have a
# clause whose dispute turned on two risk types at once. `gold` stays as the
# coarse risky/not_risky so the binary panel needs no lookup.
#
# `issues` carries the model's whole issue list as compact JSON. `prob_type1`
# and `prob_type2` are derived from it — the strongest issue of each type — and
# are what the panels score, so the scoring path never parses the list again.
FIELDS = ["contract_id", "citation", "clause_id", "clause_name",
          "label", "taxonomy", "taxonomy_provenance",
          "gold", "gold_type1", "gold_type2", "gold_subtype", "pred",
          "prob_type1", "prob_type2", "n_issues_type1", "n_issues_type2",
          "issues", "ok"]

TYPE_NAME = {
    "1.1": "risk type 1.1 — lexical ambiguity or vagueness",
    "1.2": "risk type 1.2 — mechanical error",
    "1.3": "risk type 1.3 — general-vs-specific / list scope",
    "2.1": "risk type 2.1 — conflicting clauses",
    "2.2": "risk type 2.2 — whole-instrument incoherence",
    "2.3": "risk type 2.3 — recitals vs operative text",
}

EXCERPT_CAP = 6_000     # characters of opinion an example may carry

# Reading a judgment the agent wrote by hand lives in `predictions.py`, which is
# also mounted into the container, so host and container cannot disagree.
issues_of = predictions.issues_of
probs_of = predictions.probs_of


# ------------------------------------------------------------------ gold ----
def gold_of(row):
    """The binary label. Positive means a court construed the clause at all."""
    return "not_risky" if row["label"] != "POSITIVE" else "risky"


def gold_types(row):
    """(is type 1, is type 2) — NOT exclusive.

    `taxonomy` is a comma-separated list of codes since step 1 began letting a
    multi-key case give a clause more than one, so a positive can be both.
    """
    if row["label"] != "POSITIVE":
        return 0, 0
    codes = [c.strip() for c in row["taxonomy"].split(",") if c.strip()]
    return (int(any(c.startswith("1") for c in codes)),
            int(any(c.startswith("2") for c in codes)))


def gold_fine(row):
    return row["taxonomy"] if row["label"] == "POSITIVE" else "none"


def _f(x, default=0.0):
    try:
        return min(max(float(x), 0.0), 1.0)
    except (TypeError, ValueError):
        return default


def _int(x, default=0):
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


def pred_from_probs(p1, p2):
    """Coarse label at FLAG. Both below -> not_risky; else the likelier type."""
    if p1 < FLAG and p2 < FLAG:
        return "not_risky"
    return "risky_type1" if p1 >= p2 else "risky_type2"


def pred_row(cid, c, judgment):
    """One `preds.csv` row for one provision. `judgment` is None if unanswered.

    The single place a row is built, so the run, its gap-filling pass and
    everything that scores the output cannot disagree about what a column means.
    An unanswered provision is written with `ok=0` and blank probabilities —
    never dropped and never silently scored, which the reader then treats as
    not_risky at 0.
    """
    g1, g2 = gold_types(c)
    issues = issues_of(judgment) if judgment else []
    p1, p2 = probs_of(judgment) if judgment else ("", "")
    return {
        "contract_id": cid, "citation": c["citation"],
        "clause_id": c["clause_id"], "clause_name": c["clause_name"],
        "label": c["label"], "taxonomy": c["taxonomy"],
        "taxonomy_provenance": c.get("taxonomy_provenance", ""),
        "gold": gold_of(c), "gold_type1": g1, "gold_type2": g2,
        "gold_subtype": gold_fine(c),
        "pred": pred_from_probs(p1, p2) if judgment else "",
        "prob_type1": p1, "prob_type2": p2,
        "n_issues_type1": sum(1 for t, _p, _x in issues if t == 1) if judgment else "",
        "n_issues_type2": sum(1 for t, _p, _x in issues if t == 2) if judgment else "",
        "issues": json.dumps(judgment.get("issues") or [], ensure_ascii=False)
                  if judgment else "",
        "ok": "1" if judgment else "0",
    }


def flat(text):
    return " ".join(str(text).split())


def codes_in(row):
    """The individual taxonomy codes on a row. `taxonomy` is comma-separated."""
    return [c for c in row["taxonomy"].split(",") if c]


def pick_examples(rows):
    """One worked pair per risk type present in the data.

    Grouped by INDIVIDUAL code, not by the `taxonomy` string. Since step 1
    began letting a multi-key case give a clause more than one code, that string
    can read `1.1,1.3` — and grouping on it would invent a seventh risk type
    that `TYPE_NAME` has no name for, and hold out a contract for each
    combination that happened to occur rather than one per code. A clause
    construed under both codes is a candidate for both.

    Deterministic, never random: per taxonomy code, the positive with the
    longest `opinion_comment` that still fits under EXCERPT_CAP (the longest in
    the corpus runs to 38,000 characters and would swamp the others). Where
    every candidate is over the cap the shortest is used whole — reasoning cut
    off mid-sentence is worse than a different example. Ties break on clause id.

    Each is paired with a clause from the SAME contract that no court construed,
    so the contrast is within a document. The pair is a scale, not a right
    answer.
    """
    by_contract = defaultdict(list)
    for r in rows:
        by_contract[r["contract_id"]].append(r)

    examples = []
    for code in sorted({c for r in rows if r["label"] == "POSITIVE"
                        for c in codes_in(r)}):
        cands = [r for r in rows
                 if r["label"] == "POSITIVE" and code in codes_in(r)
                 and r["opinion_comment"].strip()]
        if not cands:
            continue
        fits = [r for r in cands if len(r["opinion_comment"]) <= EXCERPT_CAP]
        best = (max(fits, key=lambda r: (len(r["opinion_comment"]), r["clause_id"]))
                if fits else
                min(cands, key=lambda r: (len(r["opinion_comment"]), r["clause_id"])))
        pool = by_contract[best["contract_id"]]
        foils = [r for r in pool if r["label"] == "NEGATIVE"]
        foil = max(foils, key=lambda r: (len(r["clause_text"]), r["clause_id"])) \
            if foils else None
        examples.append({"code": code, "row": best, "foil": foil,
                         "n_pos": len(pool) - len(foils), "n_neg": len(foils)})
    return examples


def anonymise(clauses):
    """Present the provisions under opaque ids, in the order they appear.

    The dataset's `pos1`/`neg14` ids would put the gold label on the door of
    every provision and group the answers at the top of the list. So the model
    sees `c001`, `c002`, ... in `source_span` order — the sequence a reader
    meets them in, carrying no signal, each provision beside its neighbours,
    which is what a risk type 2 judgement needs.

    The mapping is translated back before anything reaches the predictions file,
    so `clause_id` in the output still joins to `dataset.csv` unchanged.

    Returns (provisions in document order, real id -> opaque, opaque -> real).
    """
    order = sorted(clauses, key=lambda c: int(c["source_span"].split("-")[0]))
    real_of = {f"c{i:03d}": c["clause_id"] for i, c in enumerate(order, 1)}
    return order, {v: k for k, v in real_of.items()}, real_of


def load_rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def by_contract(rows):
    """contract_id -> every clause of it, largest first so a bad call fails early."""
    g = defaultdict(list)
    for r in rows:
        g[r["contract_id"]].append(r)
    return OrderedDict(sorted(g.items(), key=lambda kv: -len(kv[1])))


def done_contracts(path, groups=None):
    """Contracts that need no further call.

    `groups` makes "done" mean COMPLETE. Without it one scored provision marks a
    contract finished, and a call that answered 1 of 85 is never revisited.
    """
    if not path.exists():
        return set()
    scored = defaultdict(set)
    for r in load_rows(path):
        if r["ok"] == "1":
            scored[r["contract_id"]].add(r["clause_id"])
    if groups is None:
        return set(scored)
    return {cid for cid, ids in scored.items()
            if len(ids) >= len(groups.get(cid, ()))}


# --------------------------------------------------------------- metrics ----
def roc_auc(scores, labels):
    """Rank-based ROC-AUC (Mann-Whitney U), ties averaged."""
    if not any(labels) or all(labels):
        return float("nan")
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j < len(order) and scores[order[j]] == scores[order[i]]:
            j += 1
        for k in range(i, j):
            ranks[order[k]] = (i + j - 1) / 2.0 + 1.0
        i = j
    n_pos = sum(labels)
    n_neg = len(labels) - n_pos
    s = sum(r for r, y in zip(ranks, labels) if y)
    return (s - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)

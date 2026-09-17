"""Spellbook's Risks & Negotiation reviews, rewritten in the agent's predictions format.

Spellbook reviewed 20 contracts on behalf of each party and raised issues —
title, description, a proposed revision — with no probability attached. The
review pass in `spellbook/risks_negotiation/review.py` located every issue in
the contract by character offset. The clause inventory was rebuilt after the
reviews were run, so the clause ids stored with them are stale, but the
offsets are into `output/contracts/<cid>.md`, which has not changed, and
`dataset.csv` records each clause's span in the same file. An issue goes to
the clause its located text overlaps most.

**Type and issue text come from `spellbook_review_types.py`**, which classifies
each issue as risk type 1, type 2 or neither and, for the typed ones, restates
the tool's finding in the dataset's own sentence form. A typed issue is written
with that restatement as its `issue`, so the alignment judge compares a
sentence in the form it was built for; the agent's worked examples showed it
that form, and Spellbook saw no examples. An issue classified neither is
written as type 0 with the tool's own words: it counts as an issue Spellbook
raised, is never sent to the judge, and is never aligned — the tool's
non-construction findings are not what the dataset records, and comparing them
against it would only score the register.

Every issue gets probability 1: Spellbook ranks nothing, so there is no
threshold to sweep, and the figure this feeds collapses to a single operating
point. The precision and recall at that point are the numbers to read.

Only the Risks & Negotiation tab is taken. The Proofread tab — spelling,
capitalisation, undefined terms — is a different product.

`spellbook/` is read and never written.

Input : spellbook/risks_negotiation/results/<cid>.json, output/dataset.csv,
        output/spellbook_review_types.json
Output: output/spellbook_review_preds.csv

Usage:
    python src/experiments/spellbook_review_types.py --parallel 4
    python src/experiments/spellbook_review_preds.py
    python src/experiments/issue_alignment_check.py \\
        --preds output/spellbook_review_preds.csv --name spellbook_review_alignment
"""
import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402
import runs  # noqa: E402

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

RESULTS = lib.ROOT / "spellbook" / "risks_negotiation" / "results"
DATASET = lib.OUT / "dataset.csv"
TYPES = lib.OUT / "spellbook_review_types.json"
OUT = lib.OUT / "spellbook_review_preds.csv"
TAB = "risks_negotiation"

TYPE = {"type1": 1, "type2": 2, "none": 0}


def span(row):
    a, b = row["source_span"].split("-")
    return int(a), int(b)


def clause_for(loc, clauses):
    """The clause whose span overlaps the located text most, or None."""
    s, e = loc["start_char"], loc["end_char"]
    best, cover = None, 0
    for c in clauses:
        a, b = span(c)
        o = min(e, b) - max(s, a)
        if o > cover:
            best, cover = c, o
    return best


def located(cid, clauses, tab=TAB):
    """(party, issue, current clause) for every issue of `tab` that lands in a clause.

    `issue['index']` is its position in the review file under its party, which
    is how `spellbook_review_types.py` keys its judgments.
    """
    review = json.loads((RESULTS / f"{cid}.json").read_text(encoding="utf-8"))
    for party, p in review["parties"].items():
        for n, it in enumerate(p["issues"]):
            if it.get("tab") != tab:
                continue
            loc = it.get("location") or {}
            if loc.get("start_char") is None:
                continue
            c = clause_for(loc, clauses)
            if c is None:
                continue
            yield party, {**it, "index": n}, c


def main():
    if not TYPES.exists():
        sys.exit(f"{TYPES} does not exist — run spellbook_review_types.py first")
    types = lib.read_json(TYPES, {})
    dataset = runs.by_contract(runs.load_rows(DATASET))
    rows, summary = [], []
    tally = Counter()
    for path in sorted(RESULTS.glob("*.json")):
        cid = path.stem
        if cid not in dataset:
            summary.append(f"  {cid[:50]:52} not in the current dataset")
            continue
        if cid not in types:
            summary.append(f"  {cid[:50]:52} not yet typed")
            continue
        clauses = dataset[cid]
        per_clause = {c["clause_id"]: [] for c in clauses}
        n_taken = n_untyped = 0
        for party, it, c in located(cid, clauses):
            j = types[cid].get(f"{party}/{it['index']}")
            if j is None:
                n_untyped += 1
                continue
            n_taken += 1
            t = TYPE[j["type"]]
            tally[t] += 1
            per_clause[c["clause_id"]].append({
                "issue": j["issue"] if t else f"{it['title']}. {it['description']}".strip(),
                "type": t, "prob": 1.0,
                "kind": it.get("kind"), "party": party,
            })
        for c in clauses:
            rows.append(runs.pred_row(cid, c, {"issues": per_clause[c["clause_id"]]}))
        summary.append(f"  {cid[:50]:52} {len(clauses):4} clauses, {n_taken:4} issues placed"
                       + (f", {n_untyped} without a type judgment" if n_untyped else ""))

    print(f"{'contract':52}")
    print("\n".join(summary))
    n_pos = sum(1 for r in rows if r["gold"] != "not_risky")
    print(f"\n{len(rows)} clauses, {n_pos} of them construed; "
          f"{sum(tally.values())} issues placed — "
          f"type 1 {tally[1]}, type 2 {tally[2]}, neither {tally[0]}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=runs.FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()

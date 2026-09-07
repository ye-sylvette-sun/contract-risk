"""Assemble and validate dataset.csv (no LLM).

One row per clause step 1 enumerated. A row is POSITIVE when step 2 named its
id, NEGATIVE otherwise — a set membership test on ids, nothing more. Both
classes come from one enumeration, cut in one call that did not know which
clause any court had construed, so they are the same kind of object and differ
in one respect: whether the opinion shows the clause was fought over. (The two
steps used to cut their own spans, and the overlap had to be computed with line
arithmetic.)

A positive's own contract always contributes negatives, so both classes come
from the same document in the same OCR condition: a classifier cannot win by
recognising style or scan quality, which is what makes it safe to carry the OCR
damage rather than repair it. The other agreements of the case contribute too —
they were before the court and not construed, and they are the only documents
where the right answer is "nothing here".

Then it validates, loudly, and refuses to write on failure: every row must
re-slice from its contract file at the recorded span and reproduce
`clause_text` exactly.

Input : output/inventory.json, output/disputes.json, output/contracts.json,
        output/cases.json
Output: output/dataset.csv

Usage:
    python src/build_dataset.py
"""
import csv
import json
from collections import Counter

import lib

FIELDS = ["citation", "taxonomy", "taxonomy_provenance", "key", "clause_id",
          "clause_name", "label", "provenance", "case_desc", "contract_id",
          "contract_file", "source_lines", "source_span", "clause_text",
          "anchor_score", "n_issues", "issues", "opinion_comment"]


def main():
    cases = lib.read_json(lib.OUT / "cases.json", {})
    inventory = lib.read_json(lib.OUT / "inventory.json", {})
    disputes = lib.read_json(lib.OUT / "disputes.json", {})
    registry = lib.read_json(lib.OUT / "contracts.json", {})
    if not inventory or not disputes:
        raise SystemExit("run step 1 and step 2 first")
    rows = []

    # Every disputed clause, keyed the way a row is keyed. This IS the label.
    positive = {(d["contract_id"], d["clause_id"]): (citation, found, d)
                for citation, found in disputes.items()
                for d in found["clauses"]}

    by_id = {(cid, c["clause_id"]): c
             for cid, inv in inventory.items() for c in inv["clauses"]}
    # Every positive's text, across every case. A negative that reproduces one
    # of these character for character is dropped below.
    positive_texts = {by_id[k]["text"] for k in positive if k in by_id}

    for cid in sorted(inventory):
        inv = inventory[cid]
        citation = inv["citation"]
        if citation not in disputes:
            continue          # step 2 has not processed this case yet
        found = disputes[citation]
        case = cases.get(citation, {})
        entry = registry[cid]

        # Fallback risk type for a contract with no positive of its own: every
        # code the CASE was filed under. Straight from the Westlaw keys, so it
        # is `westlaw` provenance however many codes there are — no model chose
        # between them, they are simply all carried.
        here = [d for d in found["clauses"] if d["contract_id"] == cid]
        case_code = ",".join(case.get("taxonomy", []))
        case_key = ",".join(sorted(case.get("keys", {})))
        # A negative carries the risk type its own contract's positives were
        # construed under, failing that the case's codes. Not a claim about the
        # negative — nothing was construed in it — but a record of what the case
        # is about, which is why its provenance follows those positives.
        neg_tax = ",".join(sorted({t for d in here
                                   for t in d["taxonomy"].split(",")})) or case_code
        neg_prov = ("model" if any(d["taxonomy_provenance"] == "model"
                                   for d in here) else "westlaw")
        neg_key = ",".join(sorted({k for d in here
                                   for k in d["key"].split(",")})) or case_key

        n_pos = n_neg = 0
        for c in inv["clauses"]:
            hit = positive.get((cid, c["clause_id"]))
            if hit is None and c["text"] in positive_texts:
                # Same words, somewhere else. A clause reproducing a positive
                # character for character carries whatever made that positive
                # risky, so labelling it NEGATIVE would assert the opposite of a
                # label the corpus already holds. It comes from boilerplate
                # repeated across endorsements, and from cases filing several
                # editions of one instrument. Dropped rather than labelled.
                print(f"    excluded {c['name']} of {cid} "
                      f"(reproduces a positive verbatim)")
                continue
            d = hit[2] if hit else None
            if d:
                n_pos += 1
            else:
                n_neg += 1
            rows.append({
                "citation": citation,
                "taxonomy": d["taxonomy"] if d else neg_tax,
                "taxonomy_provenance": (d["taxonomy_provenance"] if d
                                        else neg_prov),
                "key": d["key"] if d else neg_key,
                "clause_id": c["clause_id"],
                "clause_name": c["name"],
                "label": "POSITIVE" if d else "NEGATIVE",
                "provenance": ("step 2 — construed by the court" if d
                               else "step 1 — not disputed"),
                "case_desc": found["case_desc"],
                "contract_id": cid,
                "contract_file": entry["file"],
                "source_lines": f"{c['lines'][0]}-{c['lines'][1]}",
                "source_span": f"{c['span'][0]}-{c['span'][1]}",
                "clause_text": c["text"],
                "anchor_score": c["score"],
                "n_issues": len(d["issues"]) if d else 0,
                # The per-issue detail, as JSON in one column: the risk type,
                # the sentence naming the defect, and the passage that shows it.
                # Issue-level scoring reads this; nothing else needs to.
                "issues": json.dumps(d["issues"], ensure_ascii=False) if d
                          else "[]",
                "opinion_comment": d["opinion_comment"] if d else "",
            })
        print(f"{cid}: {n_pos} positive, {n_neg} negative")

    # A clause step 2 named that step 1 never listed would be a silent hole.
    # `check()` rejects those at source, so this asserts the invariant holds
    # rather than expecting to fire.
    orphan = [k for k in positive if k not in by_id]
    assert not orphan, f"{len(orphan)} disputed clause(s) not in the inventory: {orphan[:3]}"

    # The uniqueness that matters is POSITIONAL: one clause must not be cut
    # twice from the same place. Asserting on `clause_text` instead would be
    # wrong — one policy repeats `All other terms ... remain unchanged` across
    # nine endorsements, and those are nine real clauses at nine spans.
    same_place = [k for k, n in Counter(
        (r["contract_id"], r["source_span"]) for r in rows).items() if n > 1]
    assert not same_place, \
        f"{len(same_place)} clause(s) extracted twice from the same span: " \
        f"{same_place[:3]}"

    # Identical text under BOTH labels is fatal: the same words cannot be
    # evidence for and against at once. Repetition within one label is fine.
    by_text = {}
    for r in rows:
        by_text.setdefault(r["clause_text"], set()).add(r["label"])
    contradictions = [t for t, labels in by_text.items() if len(labels) > 1]
    assert not contradictions, \
        f"{len(contradictions)} clause text(s) appear as both POSITIVE and " \
        f"NEGATIVE: {[t[:60] for t in contradictions[:3]]}"

    files = {}
    for r in rows:
        path = lib.ROOT / r["contract_file"]
        text = files.setdefault(path, path.read_text(encoding="utf-8"))
        s0, s1 = (int(x) for x in r["source_span"].split("-"))
        assert lib.normalise(text[s0:s1]) == r["clause_text"], \
            f"{r['clause_id']} of {r['contract_id']} no longer cuts from its file"
        assert r["label"] == "NEGATIVE" or r["opinion_comment"], \
            f"{r['clause_id']} of {r['contract_id']} has no opinion passage"
        assert r["label"] == "NEGATIVE" or r["n_issues"] >= 1, \
            f"{r['clause_id']} of {r['contract_id']} is positive with no issue"

    path = lib.OUT / "dataset.csv"
    # `lineterminator="\n"`: csv defaults to CRLF on every platform, so the
    # committed dataset.csv (LF) did not byte-match what a rebuild produced, and
    # the two hashed differently in the run manifests. The rows were identical;
    # the file was not. Now a rebuild reproduces the committed artifact exactly.
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)

    pos = [r for r in rows if r["label"] == "POSITIVE"]
    scores = [float(r["anchor_score"]) for r in rows]
    issues = sum(r["n_issues"] for r in pos)
    print(f"\n{len(rows)} rows | {len(pos)} positive / {len(rows) - len(pos)} "
          f"negative | {len({r['contract_id'] for r in rows})} contracts")
    print(f"{issues} issues over {len(pos)} positives — the issue-level "
          f"denominator")
    spread = Counter(r["n_issues"] for r in pos)
    if spread:
        print("  issues per positive: " + ", ".join(
            f"{n}x{spread[n]}" for n in sorted(spread)))
    if scores:
        print(f"anchor score: {min(scores):.2f} worst, "
              f"{sum(scores) / len(scores):.3f} mean, "
              f"{sum(1 for s in scores if s == 1.0)} exact")

    # Both classes are cut by one call that did not know the labels, so a gap
    # here is a fact about the corpus rather than an artifact of the pipeline.
    # It is still the number to watch, so every build prints it.
    for name, v in (("positive", [len(r["clause_text"]) for r in pos]),
                    ("negative", [len(r["clause_text"]) for r in rows
                                  if r["label"] == "NEGATIVE"])):
        if v:
            v = sorted(v)
            print(f"  {name} length: median {v[len(v) // 2]:,} "
                  f"mean {sum(v) / len(v):,.0f} max {v[-1]:,}")

    # Reported, not asserted: repeated boilerplate is a property of the source.
    # Dedupe on `clause_text` if you want one row per distinct wording.
    rep = Counter(r["clause_text"] for r in rows)
    dup_texts = {t: n for t, n in rep.items() if n > 1}
    if dup_texts:
        print(f"repeated wording: {len(dup_texts)} text(s) appear more than "
              f"once ({sum(dup_texts.values())} rows, worst x{max(dup_texts.values())})"
              f" — boilerplate repeated across endorsements, at distinct spans")
    print(f"  -> {path.relative_to(lib.ROOT)}")


if __name__ == "__main__":
    main()

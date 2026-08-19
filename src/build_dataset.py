"""Assemble and validate dataset.csv (no LLM).

Positives are the clauses step 1 found. Negatives are every other clause whose
line window does not intersect a positive's — exact set arithmetic, not a
similarity score.

A positive's own contract always contributes negatives, so both classes come
from the same document in the same OCR condition: a classifier cannot win by
recognising style or scan quality, which is what makes it safe to carry the OCR
damage rather than repair it. The other agreements of the case contribute too —
they were before the court and not construed, and they are the only documents
where the right answer is "nothing here".

Then it validates, loudly, and refuses to write on failure: every row must
re-slice from its contract file at the recorded span and reproduce
`clause_text` exactly.

Input : output/clauses.json, output/inventory.json, output/contracts.json
Output: output/dataset.csv

Usage:
    python src/build_dataset.py
"""
import csv
from collections import Counter, defaultdict

import lib

FIELDS = ["citation", "taxonomy", "key", "clause_id", "clause_name", "label",
          "provenance", "case_desc", "contract_id", "contract_file",
          "source_lines", "source_span", "clause_text", "anchor_score",
          "opinion_comment"]


def row(citation, case_desc, entry, c, clause_id, label, provenance,
        taxonomy, key, comment=""):
    return {
        "citation": citation, "taxonomy": taxonomy, "key": key,
        "clause_id": clause_id, "clause_name": c.get("clause_name") or c["name"],
        "label": label, "provenance": provenance, "case_desc": case_desc,
        "contract_id": c["contract_id"], "contract_file": entry["file"],
        "source_lines": f"{c['lines'][0]}-{c['lines'][1]}",
        "source_span": f"{c['span'][0]}-{c['span'][1]}",
        "clause_text": c["text"], "anchor_score": c["score"],
        "opinion_comment": comment,
    }


def main():
    cases = lib.read_json(lib.OUT / "cases.json", {})
    clauses = lib.read_json(lib.OUT / "clauses.json", {})
    inventory = lib.read_json(lib.OUT / "inventory.json", {})
    registry = lib.read_json(lib.OUT / "contracts.json", {})
    rows = []

    of_case = {}
    for cid, entry in registry.items():
        of_case.setdefault(entry["citation"], []).append(cid)

    for citation, found in sorted(clauses.items()):
        case = cases.get(citation, {})
        # Fallback risk type for a contract with no positive of its own. Still
        # the Westlaw keys, never a model's choice.
        case_code = ",".join(case.get("taxonomy", []))
        case_key = ",".join(sorted(case.get("keys", {})))

        # `clause_id` is numbered WITHIN ITS CONTRACT, in document order, so
        # adding an agreement cannot renumber a clause in a document that did
        # not change, and the id depends only on the located boundaries. Unique
        # per (contract_id, clause_id), which is how every consumer keys.
        by_pos = defaultdict(list)
        for c in found["clauses"]:
            by_pos[c["contract_id"]].append(c)
        # The code is per clause, from the case's own Westlaw keys.
        for cid in sorted(by_pos):
            for i, c in enumerate(sorted(by_pos[cid], key=lambda c: c["span"]), 1):
                rows.append(row(citation, found["case_desc"], registry[cid], c,
                                f"pos{i}", "POSITIVE",
                                "step 1 — construed by the court",
                                c["taxonomy"], c["key"], c["opinion_comment"]))

        # Every inventoried contract contributes negatives, not only those that
        # produced a positive.
        n_case = 0
        for cid in sorted(c for c in of_case.get(citation, [])
                          if c in inventory):
            n = 0
            here = [c for c in found["clauses"] if c["contract_id"] == cid]
            positives = [c["lines"] for c in here]
            # A negative carries the risk type its own contract's positives
            # were construed under; failing that, the case's code.
            taxonomy = ",".join(sorted({c["taxonomy"] for c in here})) or case_code
            key = ",".join(sorted({k for c in here
                                   for k in c["key"].split(",")})) or case_key
            for c in sorted(inventory[cid]["clauses"], key=lambda c: c["span"]):
                hit = next((q for q in positives if lib.overlaps(c["lines"], q)),
                           None)
                if hit:
                    print(f"    excluded {c['name']} (lines {c['lines'][0]}-"
                          f"{c['lines'][1]} meet a positive at {hit[0]}-{hit[1]})")
                    continue
                n += 1
                n_case += 1
                rows.append(row(citation, found["case_desc"], registry[cid],
                                {**c, "contract_id": cid}, f"neg{n}", "NEGATIVE",
                                "step 2 — not disputed", taxonomy, key))

        missing = [c["contract_id"] for c in found["clauses"]
                   if c["contract_id"] not in inventory]
        for cid in sorted(set(missing)):
            print(f"  ! {cid} produced a positive but has no inventory — "
                  f"run step 2")
        print(f"{citation}: {len(found['clauses'])} positive, {n_case} negative")

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
            f"{r['clause_id']} of {r['citation']} no longer cuts from its file"
        assert r["label"] == "NEGATIVE" or r["opinion_comment"], \
            f"{r['clause_id']} of {r['citation']} has no opinion passage"

    path = lib.OUT / "dataset.csv"
    # `lineterminator="\n"`: csv defaults to CRLF on every platform, so the
    # committed dataset.csv (LF) did not byte-match what a rebuild produced, and
    # the two hashed differently in the run manifests. The rows were identical;
    # the file was not. Now a rebuild reproduces the committed artifact exactly.
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)

    pos = sum(1 for r in rows if r["label"] == "POSITIVE")
    scores = [float(r["anchor_score"]) for r in rows]
    print(f"\n{len(rows)} rows | {pos} positive / {len(rows) - pos} negative | "
          f"{len({r['contract_id'] for r in rows})} contracts")
    if scores:
        print(f"anchor score: {min(scores):.2f} worst, "
              f"{sum(scores) / len(scores):.3f} mean, "
              f"{sum(1 for s in scores if s == 1.0)} exact")

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

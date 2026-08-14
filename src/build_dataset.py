"""Assemble and validate dataset.csv (no LLM).

Positives are the clauses step 1 found. Negatives are every *other* clause of
the contracts those positives came from — "other" meaning every clause whose
line window does not intersect a positive's. That is exact set arithmetic, not a
similarity score: the section containing the disputed sub-clause contains its
lines, so it is excluded and the exclusion is printed.

Negatives come from the same contract as the positives on purpose. The two
classes are then the same document, in the same OCR condition, drafted by the
same parties — so a classifier cannot win by recognising document style, and
neither can it win by recognising scan quality, which is what makes it safe to
carry the OCR damage into the dataset instead of repairing it.

Then it validates, loudly, and refuses to write on failure. The gate is that
every row re-slices: reading its contract file at the recorded character span
and normalising must reproduce `clause_text` exactly. Nothing is taken on trust
from the step artifacts.

Input : output/clauses.json, output/inventory.json, output/contracts.json
Output: output/dataset.csv

Usage:
    python src/build_dataset.py
"""
import csv
from collections import Counter

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
    clauses = lib.read_json(lib.OUT / "clauses.json", {})
    inventory = lib.read_json(lib.OUT / "inventory.json", {})
    registry = lib.read_json(lib.OUT / "contracts.json", {})
    rows = []

    for citation, found in sorted(clauses.items()):
        if not found["clauses"]:
            print(f"{citation}: no positives — skipped")
            continue

        # The code is per clause: which risk type the court's construction of
        # THIS clause turned on, drawn from the case's own Westlaw keys and
        # validated against them in step 1. Never a model's free choice.
        for i, c in enumerate(found["clauses"], 1):
            rows.append(row(citation, found["case_desc"],
                            registry[c["contract_id"]], c, f"pos{i}", "POSITIVE",
                            "step 1 — construed by the court",
                            c["taxonomy"], c["key"], c["opinion_comment"]))

        # Step 2 runs on every contract that produced a positive, so every
        # positive has negatives from its own document.
        n = 0
        for cid in sorted({c["contract_id"] for c in found["clauses"]}):
            if cid not in inventory:
                print(f"  ! {cid} produced a positive but has no inventory — "
                      f"run step 2")
                continue
            here = [c for c in found["clauses"] if c["contract_id"] == cid]
            positives = [c["lines"] for c in here]
            # A negative carries the risk type its own contract's positives were
            # construed under — that is what it is a negative of.
            taxonomy = ",".join(sorted({c["taxonomy"] for c in here}))
            key = ",".join(sorted({k for c in here for k in c["key"].split(",")}))
            for c in inventory[cid]["clauses"]:
                hit = next((q for q in positives if lib.overlaps(c["lines"], q)),
                           None)
                if hit:
                    print(f"    excluded {c['name']} (lines {c['lines'][0]}-"
                          f"{c['lines'][1]} meet a positive at {hit[0]}-{hit[1]})")
                    continue
                n += 1
                rows.append(row(citation, found["case_desc"], registry[cid],
                                {**c, "contract_id": cid}, f"neg{n}", "NEGATIVE",
                                "step 2 — not disputed", taxonomy, key))

        print(f"{citation}: {len(found['clauses'])} positive, {n} negative")

    # Validate, loudly.
    dupes = [t for t, k in Counter(r["clause_text"] for r in rows).items() if k > 1]
    assert not dupes, f"{len(dupes)} duplicate clause texts in the dataset"
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
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
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
    print(f"  -> {path.relative_to(lib.ROOT)}")


if __name__ == "__main__":
    main()

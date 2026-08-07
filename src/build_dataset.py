"""Assemble and validate dataset.csv (no LLM).

Positives are the clauses step C found. Negatives are every other provision of
the instrument those clauses came from — "every other" meaning every provision
whose line window does not intersect a positive's. That is exact set arithmetic,
not a similarity score: the section containing the disputed subsection contains
its lines, so it is excluded.

Input : output/clauses.json, output/contracts.json, output/contracts/*.md
Output: output/dataset.csv

Usage:
    conda run -n legal-llm python src/build_dataset.py
"""
import csv
import difflib
from collections import Counter

import lib

FIELDS = ["citation", "taxonomy", "key", "clause_id", "clause_name", "label",
          "provenance", "case_desc", "contract_id", "contract_file",
          "source_lines", "clause_text", "clause_text_raw", "clause_repairs",
          "opinion_comment"]


def repaired_words(raw, clean):
    """How many words the repair actually changed."""
    a, b = raw.split(), clean.split()
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return sum(max(i2 - i1, j2 - j1)
               for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag == "replace")


def main():
    clauses = lib.read_json(lib.OUT / "clauses.json", {})
    registry = lib.read_json(lib.OUT / "contracts.json", {})
    rows, changed, total = [], 0, 0

    for citation, case in clauses.items():
        if not case["clauses"]:
            print(f"{citation}: no positives — skipped")
            continue

        for i, c in enumerate(case["clauses"], 1):
            rows.append({
                "citation": citation, "taxonomy": c["taxonomy"], "key": c["key"],
                "clause_id": f"pos{i}", "clause_name": c["clause_name"],
                "label": "POSITIVE", "provenance": "step C — construed by the court",
                "case_desc": case["case_desc"], "contract_id": c["contract_id"],
                "contract_file": registry[c["contract_id"]]["file"],
                "source_lines": f"{c['lines'][0]}-{c['lines'][1]}",
                "clause_text": c["text"], "clause_text_raw": c["text_raw"],
                "clause_repairs": c["repairs"],
                "opinion_comment": c["opinion_comment"],
            })
            changed += repaired_words(c["text_raw"], c["text"])
            total += len(c["text"].split())

        # Negatives come out of the instrument the positives came from: the two
        # classes have to be the same document.
        cid = Counter(c["contract_id"] for c in case["clauses"]).most_common(1)[0][0]
        entry = registry[cid]
        positives = [c["lines"] for c in case["clauses"] if c["contract_id"] == cid]

        n, skipped = 0, []
        for p in entry["provisions"]:
            hit = next((q for q in positives if lib.overlaps(p["lines"], q)), None)
            if hit:
                skipped.append(f"{p['name']} (lines {p['lines'][0]}-{p['lines'][1]} "
                               f"meet a positive at {hit[0]}-{hit[1]})")
                continue
            n += 1
            lines = (lib.ROOT / entry["file"]).read_text(encoding="utf-8").split("\n")
            raw = lib.window(lines, *p["lines"])
            rows.append({
                "citation": citation, "taxonomy": c["taxonomy"], "key": c["key"],
                "clause_id": f"neg{n}", "clause_name": p["name"],
                "label": "NEGATIVE", "provenance": "step B — not disputed",
                "case_desc": case["case_desc"], "contract_id": cid,
                "contract_file": entry["file"],
                "source_lines": f"{p['lines'][0]}-{p['lines'][1]}",
                "clause_text": p["text"], "clause_text_raw": raw,
                "clause_repairs": p["repairs"], "opinion_comment": "",
            })
            changed += repaired_words(raw, p["text"])
            total += len(p["text"].split())

        print(f"{citation}: {len(case['clauses'])} positive, {n} negative "
              f"from {cid}")
        for s in skipped:
            print(f"    excluded {s}")

    # Validate, loudly.
    dupes = [t for t, k in Counter(r["clause_text"] for r in rows).items() if k > 1]
    assert not dupes, f"{len(dupes)} duplicate clause texts in the dataset"
    for r in rows:
        lines = (lib.ROOT / r["contract_file"]).read_text(encoding="utf-8").split("\n")
        lo, hi = (int(x) for x in r["source_lines"].split("-"))
        assert lib.verify(lines, lo, hi, r["clause_text"]) is None, \
            f"{r['clause_id']} of {r['citation']} no longer verifies"
        assert r["label"] == "NEGATIVE" or r["opinion_comment"], \
            f"{r['clause_id']} of {r['citation']} has no opinion passage"

    path = lib.OUT / "dataset.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    pos = sum(1 for r in rows if r["label"] == "POSITIVE")
    print(f"\n{len(rows)} rows | {pos} positive / {len(rows) - pos} negative")
    print(f"repaired words: {changed:,} of {total:,} "
          f"({changed / total * 100:.2f}%)" if total else "")
    print(f"  -> {path.relative_to(lib.ROOT)}")


if __name__ == "__main__":
    main()

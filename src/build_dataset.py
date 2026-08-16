"""Assemble and validate dataset.csv (no LLM).

Positives are the clauses step 1 found. Negatives are every *other* clause of
every contract of the same case — "other" meaning every clause whose line window
does not intersect a positive's. That is exact set arithmetic, not a similarity
score: the section containing the disputed sub-clause contains its lines, so it
is excluded and the exclusion is printed.

A positive's own contract always contributes negatives, on purpose. Those two
classes are then the same document, in the same OCR condition, drafted by the
same parties — so a classifier cannot win by recognising document style, and
neither can it win by recognising scan quality, which is what makes it safe to
carry the OCR damage into the dataset instead of repairing it.

The other agreements of the case contribute negatives too. A case often files
several and the court reaches only some; the rest were before the court and were
not construed, which is what a negative is. They also supply the only documents
in which the right answer is "nothing here" — without them an evaluation cannot
see a model that flags something in every contract it is given.

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
        # For a contract with no positive of its own, the risk type is the
        # CASE's — still the Westlaw keys, never a model's choice. Every case in
        # scope is filed under exactly one code, so this is unambiguous.
        case_code = ",".join(case.get("taxonomy", []))
        case_key = ",".join(sorted(case.get("keys", {})))

        # `clause_id` is numbered WITHIN ITS CONTRACT, in document order.
        #
        # Both halves of that matter. Scoping it to the contract means adding
        # another agreement to the case cannot renumber a clause in a document
        # that did not change — with case-scoped ids, one new contract sorting
        # early shifts every id after it, and every prediction keyed on those ids
        # silently points at the wrong clause. Ordering by character span means
        # the id is a function of the document and the located boundaries alone,
        # not of the order a model happened to answer in.
        #
        # It is unique per (contract_id, clause_id), which is how every consumer
        # keys, not per clause_id alone.
        by_pos = defaultdict(list)
        for c in found["clauses"]:
            by_pos[c["contract_id"]].append(c)
        # The code is per clause: which risk type the court's construction of
        # THIS clause turned on, drawn from the case's own Westlaw keys and
        # validated against them in step 1. Never a model's free choice.
        for cid in sorted(by_pos):
            for i, c in enumerate(sorted(by_pos[cid], key=lambda c: c["span"]), 1):
                rows.append(row(citation, found["case_desc"], registry[cid], c,
                                f"pos{i}", "POSITIVE",
                                "step 1 — construed by the court",
                                c["taxonomy"], c["key"], c["opinion_comment"]))

        # Every inventoried contract of the case contributes negatives, not only
        # the ones that produced a positive. An agreement filed in the case that
        # the court never construed is a negative document: its clauses were
        # before the court and were not disputed.
        n_case = 0
        for cid in sorted(c for c in of_case.get(citation, [])
                          if c in inventory):
            n = 0
            here = [c for c in found["clauses"] if c["contract_id"] == cid]
            positives = [c["lines"] for c in here]
            # A negative carries the risk type its own contract's positives were
            # construed under — that is what it is a negative of. Where the
            # contract has none, it falls back to the case's code.
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

    # Validate, loudly.
    #
    # The uniqueness that matters is POSITIONAL: one clause must not be cut
    # twice out of the same place. This used to assert on `clause_text`
    # instead, on the assumption that identical text means the same clause
    # extracted twice. That assumption is false, and an insurance policy breaks
    # it immediately: every endorsement of one policy carried a verbatim `All
    # other terms, conditions and limitations of this Policy shall remain
    # unchanged`, and one boilerplate ran to nine copies. Those are nine real
    # clauses at nine distinct spans, and refusing to write them would have been
    # the pipeline lying about the document.
    same_place = [k for k, n in Counter(
        (r["contract_id"], r["source_span"]) for r in rows).items() if n > 1]
    assert not same_place, \
        f"{len(same_place)} clause(s) extracted twice from the same span: " \
        f"{same_place[:3]}"

    # Identical text under BOTH labels is a different matter — the same words
    # cannot be evidence for and against at once, and no downstream consumer
    # could resolve it. That is fatal; repetition within one label is not.
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

    # Reported, not asserted: repeated boilerplate is a property of the source
    # documents. A consumer that wants one row per distinct wording can dedupe
    # on `clause_text`; the count is printed so nobody has to discover it.
    rep = Counter(r["clause_text"] for r in rows)
    dup_texts = {t: n for t, n in rep.items() if n > 1}
    if dup_texts:
        print(f"repeated wording: {len(dup_texts)} text(s) appear more than "
              f"once ({sum(dup_texts.values())} rows, worst x{max(dup_texts.values())})"
              f" — boilerplate repeated across endorsements, at distinct spans")
    print(f"  -> {path.relative_to(lib.ROOT)}")


if __name__ == "__main__":
    main()

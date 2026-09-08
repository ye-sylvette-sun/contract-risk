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
from collections import Counter, defaultdict

import lib


# The scopes a reader holding the contract and its siblings can reach. An
# `external` issue turns on case-specific material the corpus does not have —
# an email, a course of dealing, an exhibit never filed, a document whose OCR
# stops early — so no harness can put it within reach, and scoring against it
# measures the record rather than the reader. General knowledge is not
# `external`: `prompts/issue_scope.md` puts the law, terms of art and ordinary
# usage on the reader's side of the line.
REACHABLE = ("clause", "contract", "case")


def scoped(issues, cid, clause_id, scope_of):
    """Step 2's issues, each carrying step 3's scope where one was recorded.

    A copy, so nothing writes back into the loaded `disputes.json`. An issue is
    matched by its position in the clause's list, which step 2 sorts and this
    step does not reorder; an issue with no scope is passed through unchanged
    rather than given a default, because "not annotated" and "visible from the
    clause alone" are different claims.
    """
    out = []
    for n, i in enumerate(issues):
        s = scope_of.get((cid, clause_id, n))
        out.append({**i, "scope": s["scope"], "scope_needs": s["needs"]}
                   if s else dict(i))
    return out

FIELDS = ["citation", "taxonomy", "taxonomy_provenance", "key", "clause_id",
          "clause_name", "label", "provenance", "case_desc", "contract_id",
          "contract_file", "context_contract_ids", "source_lines",
          "source_span", "clause_text", "anchor_score", "n_issues", "issues",
          "opinion_comment"]


def main():
    cases = lib.read_json(lib.OUT / "cases.json", {})
    inventory = lib.read_json(lib.OUT / "inventory.json", {})
    disputes = lib.read_json(lib.OUT / "disputes.json", {})
    registry = lib.read_json(lib.OUT / "contracts.json", {})
    if not inventory or not disputes:
        raise SystemExit("run step 1 and step 2 first")
    rows = []

    # The other documents of the same case, which is exactly what step 2 was
    # shown when it decided what the court construed. A reader given only the
    # target contract cannot see an issue that lives in the fit between two of
    # them, so the set is recorded per row rather than left to be rederived
    # from the id prefix — `contracts.json` holds 14 documents step 1 never
    # inventoried, and those were not before the annotator either.
    siblings = defaultdict(list)
    for cid, inv in inventory.items():
        if inv["clauses"]:
            siblings[inv["citation"]].append(cid)

    # Step 3, if it has run. Optional by construction: it annotates issues and
    # changes no label, so a build without it produces what it always did.
    scope_of = {(r["contract_id"], r["clause_id"], r["issue_index"]): r
                for v in lib.read_json(lib.OUT / "issue_scope.json", {}).values()
                for r in v.get("scopes", [])}
    if scope_of:
        out = Counter(r["scope"] for r in scope_of.values())
        print(f"issue_scope.json: {len(scope_of)} issue(s) carry a scope — "
              + ", ".join(f"{k} {out[k]}" for k in REACHABLE + ("external",)))

    # Every disputed clause, keyed the way a row is keyed. This IS the label.
    positive = {(d["contract_id"], d["clause_id"]): (citation, found, d)
                for citation, found in disputes.items()
                for d in found["clauses"]}

    by_id = {(cid, c["clause_id"]): c
             for cid, inv in inventory.items() for c in inv["clauses"]}
    # Every positive's text, across every case. A negative that reproduces one
    # of these character for character is dropped below.
    positive_texts = {by_id[k]["text"] for k in positive if k in by_id}

    # A positive whose every issue is out of reach takes its whole contract with
    # it. Dropping the clause alone would not do: the experiment shows the model
    # the entire contract and asks it to judge the provisions listed, so a hole
    # in that list is a clause the model can read and is never asked about — and
    # the hole would sit exactly where a court found a defect. Judging part of a
    # contract is not the task, so the contract leaves the corpus.
    unreachable = {}
    for (cid, clause_id), (_, _, d) in positive.items():
        if not any(scope_of.get((cid, clause_id, n), {}).get("scope", "clause")
                   in REACHABLE for n in range(len(d["issues"]))):
            unreachable.setdefault(cid, []).append(clause_id)
    if unreachable:
        print(f"\n{len(unreachable)} contract(s) dropped whole: a clause the court "
              f"construed has no issue a contract reader could reach")
        for cid in sorted(unreachable):
            print(f"    {cid}  ({', '.join(sorted(unreachable[cid]))})")
        print()

    for cid in sorted(inventory):
        if cid in unreachable:
            continue
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

            # Step 3's verdict, applied. An issue out of reach is dropped; a
            # positive keeps the row as long as one issue survives, and the
            # whole-contract skip above has already removed the ones where none
            # does, so this cannot empty a positive's list.
            issues = [i for i in (scoped(hit[2]["issues"], cid, c["clause_id"],
                                         scope_of) if hit else [])
                      if i.get("scope", "clause") in REACHABLE]
            assert not hit or issues, f"{cid}/{c['clause_id']} kept with no issue"

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
                "context_contract_ids": ",".join(
                    x for x in sorted(siblings[citation]) if x != cid),
                "source_lines": f"{c['lines'][0]}-{c['lines'][1]}",
                "source_span": f"{c['span'][0]}-{c['span'][1]}",
                "clause_text": c["text"],
                "anchor_score": c["score"],
                "n_issues": len(issues),
                # The per-issue detail, as JSON in one column: the risk type,
                # the sentence naming the defect, the passage that shows it,
                # and — where step 3 has run — what a reader must hold to see
                # it at all. Issue-level scoring reads this; nothing else does.
                "issues": json.dumps(issues, ensure_ascii=False) if d else "[]",
                # Rebuilt from the issues that survived, not copied from step
                # 2: a passage whose only issue was dropped has nothing left to
                # explain, and leaving it would show the reader a defect the
                # row no longer claims. Duplicates fold, as they do in step 2.
                "opinion_comment": "\n\n".join(
                    dict.fromkeys(i["opinion_comment"] for i in issues)),
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

    fam = [r for r in rows if r["context_contract_ids"]]
    print(f"{len(fam)} row(s) in {len({r['contract_id'] for r in fam})} contracts "
          f"have sibling documents in their case; "
          f"{len(rows) - len(fam)} stand alone")

    # What a one-document reader can and cannot reach, where step 3 has run.
    # `case` is the part mounting the siblings recovers; `external` is the part
    # nothing recovers, and belongs in the limits rather than in a score.
    scopes = [(i.get("risk_type", ""), i["scope"])
              for r in pos for i in json.loads(r["issues"]) if "scope" in i]
    if scopes:
        for code, label in (("1", "risk type 1"), ("2", "risk type 2")):
            sub = [s for t, s in scopes if t.startswith(code)]
            if sub:
                c = Counter(sub)
                print(f"  {label} issue scope: " + "  ".join(
                    f"{k} {c[k]} ({100 * c[k] / len(sub):.0f}%)"
                    for k in ("clause", "contract", "case", "external") if c[k]))
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

"""Step 1 — locate the clauses the parties disputed (one LLM call per case).

The core judgment step. The model is given the opinion and every document
registered for the case, and it says which clauses the parties disputed and
where they sit: a line range plus a short verbatim anchor at each end. A clause
is positive because the opinion shows the two sides fought over it and the court
discussed it — whatever the court decided. Reaching litigation at all is what
makes a clause risky, so one the court examined and upheld counts just as much.

The model does not write the clause text and it does not choose the label: the
taxonomy codes come from the Westlaw keys the case was selected under and are
facts about the case, not answers. Every clause must also point at the passage
of the opinion that shows the dispute, which is what keeps clause selection tied
to the court's own words rather than to what looks risky to a model of the same
family as the ones under evaluation.

Input : output/cases.json, output/contracts.json, output/contracts/*.md,
        output/opinions/<id>.txt
Output: output/clauses.json  (resumable — re-runs only what is missing)

Usage:
    python src/step1_extract.py [--case CITATION]
"""
import argparse

import lib

OUT = lib.OUT / "clauses.json"


def blocks(ids, texts):
    """The registered documents for one case, numbered and delimited.

    The marker names the contract_id at both ends, so the id the model must
    quote back is never more than a screen away from the lines it is reading.
    """
    return "\n\n\n".join(
        f"---------- CONTRACT {cid} START ----------\n"
        f"{lib.numbered(texts[cid])}\n"
        f"---------- CONTRACT {cid} END ----------"
        for cid in ids)


def check(clause, case, texts, opinion_lines):
    """Locate one reported clause, or say why it is rejected."""
    cid = clause["contract_id"]
    if cid not in texts:
        return None, f"contract_id {cid!r} is not one of the supplied documents"

    # The model does not choose the label — the codes are handed to it as facts
    # about the case, and one outside that set is rejected. This is an integrity
    # check on which risk type the dispute falls under, NOT a filter on whether
    # a clause was disputed: a clause the court examined and upheld is still a
    # positive, so nothing here may turn on how the case came out.
    codes = lib.codes_of(case)
    if clause["taxonomy"] not in codes:
        return None, f"taxonomy {clause['taxonomy']!r} is not one of {codes}"

    found, why = lib.locate(texts[cid], clause["start_line"], clause["end_line"],
                            clause["head"], clause["tail"])
    if why:
        return None, why

    o1 = clause["opinion_comment_start_line"]
    o2 = clause["opinion_comment_end_line"]
    if not 1 <= o1 <= o2 <= len(opinion_lines):
        return None, (f"opinion lines {o1}-{o2} are outside the opinion "
                      f"(1-{len(opinion_lines)})")

    # The key is the label's provenance: the Westlaw keys of this case that map
    # to the code the clause carries, so a row can always be traced back to the
    # headnotes it was selected under.
    keys = sorted(k for k in case["keys"]
                  if lib.KEY_BY_LABEL[k][1] == clause["taxonomy"])
    return {
        "clause_name": clause["clause_name"],
        "taxonomy": clause["taxonomy"],
        "key": ",".join(keys),
        "contract_id": cid,
        "claimed_lines": [clause["start_line"], clause["end_line"]],
        "lines": found["lines"],
        "span": found["span"],
        "score": found["score"],
        "head": clause["head"],
        "tail": clause["tail"],
        "text": found["text"],
        "opinion_lines": [o1, o2],
        "opinion_comment": lib.window(opinion_lines, o1, o2),
    }, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", help="extract one citation only")
    args = ap.parse_args()

    cases = lib.read_json(lib.OUT / "cases.json", {})
    registry = lib.read_json(lib.OUT / "contracts.json", {})
    done = lib.read_json(OUT, {})

    by_case = {}
    for cid, c in registry.items():
        by_case.setdefault(c["citation"], []).append(cid)

    for citation, ids in sorted(by_case.items()):
        if (args.case and citation != args.case) or citation in done:
            continue
        case = cases[citation]
        opinion = (lib.OPINIONS / f"{case['id']}.txt").read_text(encoding="utf-8")
        opinion_lines = opinion.split("\n")
        texts = {cid: (lib.ROOT / registry[cid]["file"]).read_text(encoding="utf-8")
                 for cid in sorted(ids)}

        contracts = blocks(sorted(ids), texts)
        # The ceiling is on the call, not on any one document: this carries
        # every document filed in the case PLUS the opinion. Sized from stored
        # artifacts, before the call is made.
        why = lib.out_of_bounds(lib.numbered(opinion) + contracts)
        if why:
            print(f"skip   {citation}: {why}")
            done[citation] = {"case_desc": f"skipped: {why}",
                              "clauses": [], "rejected": []}
            lib.write_json(OUT, done)
            continue

        print(f"extract {citation}  ({len(ids)} document(s))")
        answer = lib.ask(
            "extract", case["id"],
            citation=citation, opinion=lib.numbered(opinion),
            contracts=contracts,
            risks=lib.risk_lines(sorted(case["keys"])),
            headnotes="\n".join(f"- [{k}] {h}"
                                for k, hs in case["keys"].items() for h in hs))
        if answer is None:
            continue

        kept, rejected = [], []
        for clause in answer["clauses"]:
            record, why = check(clause, case, texts, opinion_lines)
            if record is None:
                rejected.append({"clause_name": clause["clause_name"],
                                 "contract_id": clause["contract_id"],
                                 "head": clause["head"], "tail": clause["tail"],
                                 "reason": why})
                print(f"  ! rejected {clause['clause_name']}: {why}")
            else:
                kept.append(record)
                moved = ("" if record["lines"] == record["claimed_lines"]
                         else f"  (snapped from {record['claimed_lines'][0]}-"
                              f"{record['claimed_lines'][1]})")
                print(f"  {record['clause_name']} [{record['taxonomy']}] "
                      f"{len(record['text'])} chars from {record['contract_id']} "
                      f"lines {record['lines'][0]}-{record['lines'][1]} "
                      f"@{record['score']:.2f}{moved}")
        if not answer["clauses"]:
            print(f"  no clause extracted: {answer['case_desc']}")
        done[citation] = {"case_desc": answer["case_desc"],
                          "clauses": kept, "rejected": rejected}
        lib.write_json(OUT, done)

    clauses = sum(len(c["clauses"]) for c in done.values())
    rejected = sum(len(c["rejected"]) for c in done.values())
    won = {c["contract_id"] for v in done.values() for c in v["clauses"]}
    print(f"{len(done)} cases | {clauses} clauses kept | {rejected} rejected | "
          f"{len(won)} contracts won")


if __name__ == "__main__":
    main()

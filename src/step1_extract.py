"""Step 1 — locate the clauses the parties disputed (one LLM call per case).

The core judgment step. Given the opinion and every contract registered for the
case, the model says which clauses the parties disputed and where: a line range
plus a verbatim anchor at each end. A clause is positive because the opinion
shows the two sides fought over it — whatever the court decided, since reaching
litigation is itself what makes it risky.

It may legitimately answer "none": the opinion can turn on an agreement that
was never filed.

The model writes no clause text and chooses no label — the taxonomy codes come
from the Westlaw keys and are facts about the case. Every clause must point at
the opinion passage showing the dispute, which keeps selection tied to the
court's words rather than to what looks risky to a model.

Contracts step 0b rejected as two-column scans are dropped before the call.

Input : output/cases.json, output/contracts.json, output/contracts/*.md,
        output/opinions/<id>.txt, output/layout.json
Output: output/clauses.json  (resumable — re-runs only what is missing)

Usage:
    python src/step1_extract.py [--case CITATION]
"""
import argparse

import lib

OUT = lib.OUT / "clauses.json"


def blocks(ids, texts):
    """The registered contracts for one case, numbered and delimited.

    The marker names the contract_id at both ends, so the id the model must
    quote back is never more than a screen away from the lines it is reading.
    """
    return "\n\n\n".join(
        f"---------- CONTRACT {cid} START ----------\n"
        f"{lib.numbered(texts[cid])}\n"
        f"---------- CONTRACT {cid} END ----------"
        for cid in ids)


def taxonomy_of(value):
    """The code the model meant. A key label maps to its code; brackets go.

    `prompts/extract.schema.json` now enumerates the six codes, so a wrong
    spelling cannot come back at all. This exists because it did: before the
    enum, one run answered with the Westlaw KEY LABEL — `k152`, `k143(2)` —
    instead of the code that label maps to, and once with `[1.1]`. All twelve
    named the right risk category in the wrong vocabulary, and two whole cases lost
    every clause they had to it.

    It concedes nothing on provenance. The result still has to be one of the
    codes the CASE was filed under, which is the check that matters: the label
    comes from the Westlaw keys, never from the model.

    The label is tried BEFORE the brackets are stripped: `k143(2)` is a key
    label whose own name ends in a bracket, and stripping first turns it into
    `k143(2` — which would have thrown away the six clauses this was written to
    recover.
    """
    s = str(value).strip()
    for candidate in (s, s.strip("[]() ")):
        if candidate in lib.KEY_BY_LABEL:
            return lib.KEY_BY_LABEL[candidate][1]
    return s.strip("[]() ")


def check(clause, case, texts, opinion_lines):
    """Locate one reported clause, or say why it is rejected."""
    cid = clause["contract_id"]
    if cid not in texts:
        return None, f"contract_id {cid!r} is not one of the supplied documents"

    # The model does not choose the label — the codes are handed to it as facts
    # about the case, and one outside that set is rejected. This is an integrity
    # check on which risk category the dispute falls under, NOT a filter on whether
    # a clause was disputed: a clause the court examined and upheld is still a
    # positive, so nothing here may turn on how the case came out.
    codes = lib.codes_of(case)
    code = taxonomy_of(clause["taxonomy"])
    if code not in codes:
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
                  if lib.KEY_BY_LABEL[k][1] == code)
    return {
        "clause_name": clause["clause_name"],
        "taxonomy": code,
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
    layout = lib.read_json(lib.OUT / "layout.json", {})
    done = lib.read_json(OUT, {})

    by_case = {}
    for cid, c in registry.items():
        by_case.setdefault(c["citation"], []).append(cid)

    for citation, ids in sorted(by_case.items()):
        if (args.case and citation != args.case) or citation in done:
            continue
        case = cases[citation]

        # A two-column scan is dropped here rather than in step 0, so the
        # registry stays a record of what was extracted and only this step's
        # view of a case narrows. An unscreened contract is used: the screen is
        # a filter on known-bad documents, not a gate that everything must pass.
        dropped = [c for c in ids if layout.get(c, {}).get("two_column")]
        ids = [c for c in ids if c not in dropped]
        for cid in dropped:
            print(f"  drop {cid}: two-column scan (step 0b)")
        if not ids:
            print(f"skip   {citation}: every contract is a two-column scan")
            done[citation] = {"case_desc": "skipped: every contract of this case "
                                           "is a two-column scan",
                              "clauses": [], "rejected": []}
            lib.write_json(OUT, done)
            continue
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

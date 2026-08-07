"""Step C — clause extraction (one LLM call per case). The core judgment step.

The model finds the provision the court construed and gives its line window in
the instrument plus the text repaired; it does not decide the risk label — the
taxonomy codes come from the Westlaw keys the case was selected under and are
handed to it as facts. Every clause must also point at the passage of the
opinion where the court construes it, which is what keeps clause selection tied
to the court's own words rather than to what looks risky to a model of the same
family as the ones under evaluation.

Input : output/cases.json, output/contracts.json, output/contracts/*.md,
        output/opinions/<id>.txt
Output: output/clauses.json  (resumable — re-runs only what is missing)

Usage:
    conda run -n legal-llm python src/step_c_extract.py [--case CITATION]
"""
import argparse

import lib

OUT = lib.OUT / "clauses.json"


def blocks(ids, registry, texts):
    """The registered instruments for one case, numbered and tagged."""
    return "\n\n\n".join(
        f"--- contract_id: {cid}\n"
        f"--- instrument: {registry[cid]['instrument']}\n\n"
        f"{lib.numbered(texts[cid])}\n\n--- end of {cid}"
        for cid in ids)


def check(clause, case, ids, texts, opinion_lines):
    """Verify one reported clause, or say why it is rejected."""
    cid = clause["contract_id"]
    if cid not in ids:
        return None, f"contract_id {cid!r} is not one of the supplied instruments"
    codes = {lib.KEY_BY_LABEL[k][1] for k in case["keys"]}
    if clause["taxonomy"] not in codes:
        return None, f"taxonomy {clause['taxonomy']!r} is not one of {sorted(codes)}"

    lines = texts[cid].split("\n")
    start, end = clause["start_line"], clause["end_line"]
    why = lib.verify(lines, start, end, clause["text"])
    if why:
        return None, why

    o1, o2 = clause["opinion_comment_start_line"], clause["opinion_comment_end_line"]
    if not 1 <= o1 <= o2 <= len(opinion_lines):
        return None, (f"opinion lines {o1}-{o2} are outside the opinion "
                      f"(1-{len(opinion_lines)})")

    keys = sorted(k for k in case["keys"]
                  if lib.KEY_BY_LABEL[k][1] == clause["taxonomy"])
    return {
        "clause_name": clause["clause_name"],
        "taxonomy": clause["taxonomy"],
        "key": ",".join(keys),
        "contract_id": cid,
        "lines": [start, end],
        "text": clause["text"],
        "text_raw": lib.window(lines, start, end),
        "repairs": clause["repairs"],
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

    for citation, ids in by_case.items():
        if (args.case and citation != args.case) or citation in done:
            continue
        case = cases[citation]
        opinion = (lib.OPINIONS / f"{case['id']}.txt").read_text(encoding="utf-8")
        opinion_lines = opinion.split("\n")
        texts = {cid: (lib.ROOT / registry[cid]["file"]).read_text(encoding="utf-8")
                 for cid in ids}

        instruments = blocks(ids, registry, texts)
        # The ceiling is on the call, not on any one instrument: this carries
        # every instrument for the case PLUS the opinion, which step B never
        # sees. Sized from stored artifacts, before the call.
        why = lib.out_of_bounds(lib.numbered(opinion) + instruments)
        if why:
            print(f"skip   {citation}: {why}")
            done[citation] = {"case_desc": f"skipped: {why}",
                              "clauses": [], "rejected": []}
            lib.write_json(OUT, done)
            continue

        print(f"extract {citation}  ({len(ids)} instrument(s))")
        answer = lib.ask(
            "extract", case["id"],
            citation=citation, opinion=lib.numbered(opinion),
            instruments=instruments,
            risks=lib.risk_lines(sorted(case["keys"])),
            headnotes="\n".join(f"- [{k}] {h}"
                                for k, hs in case["keys"].items() for h in hs))
        if answer is None:
            continue

        kept, rejected = [], []
        for clause in answer["clauses"]:
            record, why = check(clause, case, ids, texts, opinion_lines)
            if record is None:
                rejected.append({"clause_name": clause["clause_name"], "reason": why})
                print(f"  ! rejected {clause['clause_name']}: {why}")
            else:
                kept.append(record)
                print(f"  {record['clause_name']} [{record['taxonomy']}] "
                      f"{len(record['text'])} chars from {record['contract_id']}"
                      f"{'  (repaired)' if record['repairs'] else ''}")
        if not answer["clauses"]:
            print(f"  no clause extracted: {answer['case_desc']}")
        done[citation] = {"case_desc": answer["case_desc"],
                          "clauses": kept, "rejected": rejected}
        lib.write_json(OUT, done)

    clauses = sum(len(c["clauses"]) for c in done.values())
    rejected = sum(len(c["rejected"]) for c in done.values())
    print(f"{len(done)} cases | {clauses} clauses kept | {rejected} rejected")


if __name__ == "__main__":
    main()

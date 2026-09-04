"""Step 1 — locate the clauses the parties disputed (one LLM call per case).

The core judgment step. Given the opinion and every contract registered for the
case, the model says which clauses the parties disputed and where: a line range
plus a verbatim anchor at each end. A clause is positive because the opinion
shows the two sides fought over it — whatever the court decided, since reaching
litigation is itself what makes it risky.

It may legitimately answer "none": the opinion can turn on an agreement that
was never filed.

The model writes no clause text, and it never decides WHETHER a clause is
positive on its own authority — every clause must point at the opinion passage
showing the dispute, which keeps selection tied to the court's words rather
than to what looks risky to a model.

The risk TYPE is drawn from the Westlaw keys the case was filed under. Where the
case carries one code that is the whole answer and no model chose it; where it
carries several, the model says which of them this clause's dispute turned on,
and may name more than one. `taxonomy_provenance` records which of the two a row
came from — `westlaw` or `model` — so a consumer can keep only the rows whose
type no model had a say in.

Contracts step 0b rejected as two-column scans are dropped before the call.

Input : output/cases.json, output/contracts.json, output/contracts/*.md,
        output/opinions/<id>.txt, output/layout.json
Output: output/clauses.json  (resumable — re-runs only what is missing)

Usage:
    python src/step1_extract.py [--case CITATION]
"""
import argparse
import concurrent.futures as cf

import lib

OUT = lib.OUT / "clauses.json"
SHARDS = "clauses"       # output/clauses/<case_id>.json while running


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
    """The codes the model meant. A key label maps to its code; brackets go.

    `prompts/extract.schema.json` enumerates the six codes, so a wrong spelling
    cannot come back at all. This exists because it did: before the enum, one
    run answered with the Westlaw KEY LABEL — `k152`, `k143(2)` — instead of the
    code that label maps to, and once with `[1.1]`. All twelve named the right
    risk type in the wrong vocabulary, and two whole cases lost every clause
    they had to it.

    It concedes nothing on provenance. Every result still has to be one of the
    codes the CASE was filed under, which is the check that matters.

    The label is tried BEFORE the brackets are stripped: `k143(2)` is a key
    label whose own name ends in a bracket, and stripping first turns it into
    `k143(2` — which would have thrown away the six clauses this was written to
    recover.

    A bare string is still accepted, not only the list the schema now asks for:
    an archived answer from before the change reads back unchanged.
    """
    values = value if isinstance(value, (list, tuple)) else [value]
    out = []
    for v in values:
        s = str(v).strip()
        code = next((lib.KEY_BY_LABEL[c][1]
                     for c in (s, s.strip("[]() ")) if c in lib.KEY_BY_LABEL),
                    s.strip("[]() "))
        if code not in out:
            out.append(code)
    return sorted(out)


def check(clause, case, texts, opinion_lines):
    """Locate one reported clause, or say why it is rejected."""
    cid = clause["contract_id"]
    if cid not in texts:
        return None, f"contract_id {cid!r} is not one of the supplied documents"

    # The answer must be a non-empty SUBSET of the codes the case was filed
    # under, and anything outside that set is rejected. This is an integrity
    # check on which risk type the dispute falls under, NOT a filter on whether
    # a clause was disputed: a clause the court examined and upheld is still a
    # positive, so nothing here may turn on how the case came out.
    #
    # Where the case carries ONE code the model has no choice and the label is
    # still a Westlaw fact. Where it carries several, the model picks which of
    # them this clause's dispute turned on — and may pick more than one, since
    # a court can find a phrase ambiguous on its face AND resolve it from the
    # whole instrument. `provenance` below records which of the two happened,
    # so a consumer can keep only the rows no model had a say in.
    codes = lib.codes_of(case)
    types = taxonomy_of(clause["taxonomy"])
    if not types or any(t not in codes for t in types):
        return None, f"taxonomy {clause['taxonomy']!r} is not a subset of {codes}"

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
    # to the codes the clause carries, so a row can always be traced back to the
    # headnotes it was selected under.
    keys = sorted(k for k in case["keys"]
                  if lib.KEY_BY_LABEL[k][1] in types)
    return {
        "clause_name": clause["clause_name"],
        "taxonomy": ",".join(types),
        "taxonomy_provenance": "westlaw" if len(codes) == 1 else "model",
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
    ap.add_argument("--parallel", type=int, default=4,
                    help="cases extracted at once (default 4)")
    args = ap.parse_args()

    cases = lib.read_json(lib.OUT / "cases.json", {})
    registry = lib.read_json(lib.OUT / "contracts.json", {})
    layout = lib.read_json(lib.OUT / "layout.json", {})
    done = {**(lib.read_json(OUT, {}) or {}), **lib.read_shards(SHARDS)}

    by_case = {}
    for cid, c in registry.items():
        by_case.setdefault(c["citation"], []).append(cid)

    todo = [(cit, ids) for cit, ids in sorted(by_case.items())
            if not (args.case and cit != args.case) and cit not in done]

    def extract_one(citation, ids):
        """One case. Everything it has to say is collected and printed in a
        single call, so concurrent workers cannot interleave inside a case."""
        case = cases[citation]
        out = []

        # A two-column scan is dropped here rather than in step 0, so the
        # registry stays a record of what was extracted and only this step's
        # view of a case narrows. An unscreened contract is used: the screen is
        # a filter on known-bad documents, not a gate that everything must pass.
        dropped = [c for c in ids if layout.get(c, {}).get("two_column")]
        ids = [c for c in ids if c not in dropped]
        out += [f"  drop {cid}: two-column scan (step 0b)" for cid in dropped]
        if not ids:
            lib.write_shard(SHARDS, case["id"], citation,
                            {"case_desc": "skipped: every contract of this case "
                                          "is a two-column scan",
                             "clauses": [], "rejected": []})
            print(f"skip   {citation}: every contract is a two-column scan",
                  flush=True)
            return

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
            lib.write_shard(SHARDS, case["id"], citation,
                            {"case_desc": f"skipped: {why}",
                             "clauses": [], "rejected": []})
            print(f"skip   {citation}: {why}", flush=True)
            return

        answer = lib.ask(
            "extract", case["id"],
            citation=citation, opinion=lib.numbered(opinion),
            contracts=contracts,
            taxonomy=lib.taxonomy_lines(),
            risks=lib.risk_lines(sorted(case["keys"])),
            headnotes="\n".join(f"- [{k}] {h}"
                                for k, hs in case["keys"].items() for h in hs))
        if answer is None:
            return

        kept, rejected = [], []
        for clause in answer["clauses"]:
            record, why = check(clause, case, texts, opinion_lines)
            if record is None:
                rejected.append({"clause_name": clause["clause_name"],
                                 "contract_id": clause["contract_id"],
                                 "head": clause["head"], "tail": clause["tail"],
                                 "reason": why})
            else:
                kept.append(record)

        # Document order within each contract, fixed here rather than left to
        # the order the model happened to report them in.
        kept.sort(key=lambda c: (c["contract_id"], c["span"]))
        rejected.sort(key=lambda r: (r["contract_id"], r["clause_name"]))

        lib.write_shard(SHARDS, case["id"], citation,
                        {"case_desc": answer["case_desc"],
                         "clauses": kept, "rejected": rejected})

        out.insert(0, f"extract {citation}  ({len(ids)} document(s))")
        for r in rejected:
            out.append(f"  ! rejected {r['clause_name']}: {r['reason']}")
        for record in kept:
            moved = ("" if record["lines"] == record["claimed_lines"]
                     else f"  (snapped from {record['claimed_lines'][0]}-"
                          f"{record['claimed_lines'][1]})")
            out.append(f"  {record['clause_name']} [{record['taxonomy']}] "
                       f"{len(record['text'])} chars from {record['contract_id']} "
                       f"lines {record['lines'][0]}-{record['lines'][1]} "
                       f"@{record['score']:.2f}{moved}")
        if not answer["clauses"]:
            out.append(f"  no clause extracted: {answer['case_desc']}")
        print("\n".join(out), flush=True)

    if todo:
        with cf.ThreadPoolExecutor(max_workers=args.parallel) as pool:
            futs = [pool.submit(extract_one, cit, ids) for cit, ids in todo]
            for fut in cf.as_completed(futs):
                fut.result()

    done = lib.merge_shards(SHARDS, OUT)

    clauses = sum(len(c["clauses"]) for c in done.values())
    rejected = sum(len(c["rejected"]) for c in done.values())
    won = {c["contract_id"] for v in done.values() for c in v["clauses"]}
    print(f"{len(done)} cases | {clauses} clauses kept | {rejected} rejected | "
          f"{len(won)} contracts won")


if __name__ == "__main__":
    main()

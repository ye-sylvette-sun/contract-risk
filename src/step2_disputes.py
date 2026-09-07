"""Step 2 — which of step 1's clauses the parties disputed (one call per case).

The judgment step. Given the opinion, every contract registered for the case,
and step 1's complete clause list for each of them, the model says which listed
clauses the parties disputed and what the court found wrong with each. A clause
is positive because the opinion shows the two sides fought over it — whatever
the court decided, since reaching litigation is itself what makes it risky.

**It answers in ids and cannot draw a boundary.** Step 1 enumerated the clauses
without seeing this opinion, so a clause's boundaries cannot shift with what a
court said about it — which they did when this step ran first and cut its own
spans. See `step1_inventory.py` for why that matters.

A clause carries a list of ISSUES, one per distinct defect the court construed
in it, each with its own risk type and its own opinion passage. Scoring a model
at the issue level needs a denominator — how many defects a court actually found
in this clause — and one passage per clause cannot supply it. Two entries mean
two different problems; a defect the court returns to later is still one issue.
The prompt states the test (would describing both need two different `issue`
sentences?); this file enforces only what is mechanical.

It may legitimately answer "none": the opinion can turn on an agreement that was
never filed. Language the court construed that no listed clause contains goes to
`unlocated`, which records the miss without inventing a clause — so the rate at
which step 1's enumeration falls short is measured rather than papered over.

The risk TYPE is drawn from the Westlaw keys the case was filed under. Where the
case carries one code that is the whole answer and no model chose it; where it
carries several, the model says which of them each issue turned on.
`taxonomy_provenance` records which of the two a row came from — `westlaw` or
`model` — so a consumer can keep only the rows whose type no model had a say in.
Note what it does NOT cover: how many issues a clause has is a model judgement
in every case, including a single-code one.

Input : output/cases.json, output/contracts.json, output/inventory.json,
        output/contracts/*.md, output/opinions/<id>.txt
Output: output/disputes.json  (resumable — re-runs only what is missing)

Usage:
    python src/step2_disputes.py [--case CITATION]
"""
import argparse
import concurrent.futures as cf
from collections import Counter, defaultdict

import lib

OUT = lib.OUT / "disputes.json"
SHARDS = "disputes"      # output/disputes/<case_id>.json while running


def blocks(ids, texts, inventory):
    """Each contract, numbered, followed by the ids it may be answered with.

    The clause list sits immediately after the document it describes rather
    than in one table at the end, so the ids are never far from the lines they
    name. The marker repeats the contract_id at both ends for the same reason.
    """
    out = []
    for cid in ids:
        index = "\n".join(
            f"  {c['clause_id']}  lines {c['lines'][0]}-{c['lines'][1]}  {c['name']}"
            for c in inventory[cid]["clauses"])
        out.append(f"---------- CONTRACT {cid} START ----------\n"
                   f"{lib.numbered(texts[cid])}\n"
                   f"---------- CONTRACT {cid} END ----------\n\n"
                   f"CLAUSES OF {cid} — answer with these ids:\n{index}")
    return "\n\n\n".join(out)


def code_of(value):
    """The code the model meant. A key label maps to its code; brackets go.

    `prompts/disputes.schema.json` enumerates the six codes, so a wrong spelling
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
    """
    s = str(value).strip()
    return next((lib.KEY_BY_LABEL[c][1]
                 for c in (s, s.strip("[]() ")) if c in lib.KEY_BY_LABEL),
                s.strip("[]() "))


def issues_of(reported, codes, opinion_lines):
    """(kept issues, reasons some were dropped) for one clause.

    Two checks, both mechanical. A code outside the ones the CASE was filed
    under is not this dataset's to assert, and a passage outside the opinion is
    not a citation. Either one drops that ISSUE rather than the clause: the
    clause was still disputed, and a bad code on one of its defects is no reason
    to lose a defect that checks out. The clause goes only if nothing survives.

    A duplicate is folded, and what makes two entries duplicates is the DEFECT
    they name — same code, same `issue` sentence — not the passage they cite.
    Keying on the passage was tried first and was wrong: courts dispose of two
    separate defects in one paragraph all the time, and it silently undid four
    real splits in the first case it ran on. Two issues sharing a passage is
    normal and stays.

    That leaves the substantive judgement — two wordings of one defect — with
    the prompt. Nothing here can tell those apart, and a similarity threshold
    pretending otherwise would quietly drop real issues.
    """
    kept, why, seen = [], [], set()
    for i in reported:
        code = code_of(i["risk_type"])
        if code not in codes:
            why.append(f"risk_type {i['risk_type']!r} is not one of {codes}")
            continue
        o1, o2 = i["opinion_comment_start_line"], i["opinion_comment_end_line"]
        if not 1 <= o1 <= o2 <= len(opinion_lines):
            why.append(f"opinion lines {o1}-{o2} are outside the opinion "
                       f"(1-{len(opinion_lines)})")
            continue
        same = (code, " ".join(i["issue"].split()).lower())
        if same in seen:
            why.append(f"duplicate issue [{code}]: {i['issue'][:60]}")
            continue
        seen.add(same)
        kept.append({"risk_type": code, "issue": i["issue"],
                     "opinion_lines": [o1, o2],
                     "opinion_comment": lib.window(opinion_lines, o1, o2)})
    # Sorted so a re-run cannot reorder them; the issue text breaks the tie two
    # issues of one code citing one passage would otherwise leave open.
    kept.sort(key=lambda i: (i["risk_type"], i["opinion_lines"], i["issue"]))
    return kept, why


def check(cid, clause_id, reported, case, inventory, opinion_lines):
    """Resolve one reported clause against step 1's list, or say why not.

    The id is the whole of the location check: there is no anchor to match and
    no range to snap, because this step never claimed a span. The verbatim
    guarantee is unchanged — `build_dataset.py` still re-cuts every row from the
    file, now at step 1's recorded span.
    """
    if cid not in inventory:
        return None, f"contract_id {cid!r} is not one of the supplied documents"
    listed = {c["clause_id"]: c for c in inventory[cid]["clauses"]}
    if clause_id not in listed:
        return None, (f"clause_id {clause_id!r} is not in the clause list for "
                      f"{cid} ({len(listed)} clauses)")

    # The codes are checked per issue, and every one must be among those the
    # case was filed under. This is an integrity check on which risk type a
    # defect falls under, NOT a filter on whether a clause was disputed: a
    # clause the court examined and upheld is still a positive, so nothing here
    # may turn on how the case came out.
    #
    # Where the case carries ONE code the model has no choice and the label is
    # still a Westlaw fact. Where it carries several, the model says which of
    # them each defect turned on. `provenance` below records which of the two
    # happened, so a consumer can keep only the rows no model had a say in.
    codes = lib.codes_of(case)
    issues, dropped = issues_of(reported, codes, opinion_lines)
    if not issues:
        return None, "; ".join(dropped) or "no issue reported"

    types = sorted({i["risk_type"] for i in issues})

    # The key is the label's provenance: the Westlaw keys of this case that map
    # to the codes the clause carries, so a row can always be traced back to the
    # headnotes it was selected under.
    keys = sorted(k for k in case["keys"] if lib.KEY_BY_LABEL[k][1] in types)
    return {
        "contract_id": cid,
        "clause_id": clause_id,
        "clause_name": listed[clause_id]["name"],
        "lines": listed[clause_id]["lines"],
        "taxonomy": ",".join(types),
        "taxonomy_provenance": "westlaw" if len(codes) == 1 else "model",
        "key": ",".join(keys),
        "issues": issues,
        # The clause-level passage, kept so a consumer that wants "what the
        # court said about this clause" need not reassemble it. Distinct issue
        # passages are joined in the order the issues are stored; where two
        # issues cite the same lines there is only one copy, because `issues_of`
        # has already folded exact duplicates.
        "opinion_comment": "\n\n".join(
            dict.fromkeys(i["opinion_comment"] for i in issues)),
        "dropped_issues": dropped,
    }, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", help="one citation only")
    ap.add_argument("--parallel", type=int, default=4,
                    help="cases processed at once (default 4)")
    args = ap.parse_args()

    cases = lib.read_json(lib.OUT / "cases.json", {})
    registry = lib.read_json(lib.OUT / "contracts.json", {})
    inventory = lib.read_json(lib.OUT / "inventory.json", {})
    done = {**(lib.read_json(OUT, {}) or {}), **lib.read_shards(SHARDS)}

    if not inventory:
        raise SystemExit("output/inventory.json is empty — run step 1 first")

    # Only what step 1 inventoried. A contract it skipped (two-column scan, or
    # over the token ceiling) has no clause list, so there are no ids to answer
    # with and nothing this step could say about it.
    by_case = defaultdict(list)
    for cid, inv in inventory.items():
        if inv["clauses"]:
            by_case[inv["citation"]].append(cid)

    todo = [(cit, sorted(ids)) for cit, ids in sorted(by_case.items())
            if not (args.case and cit != args.case) and cit not in done]

    def one(citation, ids):
        """One case. Everything it has to say is collected and printed in a
        single call, so concurrent workers cannot interleave inside a case."""
        case = cases[citation]
        opinion = (lib.OPINIONS / f"{case['id']}.txt").read_text(encoding="utf-8")
        opinion_lines = opinion.split("\n")
        texts = {cid: (lib.ROOT / registry[cid]["file"]).read_text(encoding="utf-8")
                 for cid in ids}
        contracts = blocks(ids, texts, inventory)

        # The ceiling is on the call, not on any one document: this carries
        # every document filed in the case, its clause list, PLUS the opinion.
        # Sized from stored artifacts, before the call is made.
        why = lib.out_of_bounds(lib.numbered(opinion) + contracts)
        if why:
            lib.write_shard(SHARDS, case["id"], citation,
                            {"case_desc": f"skipped: {why}", "clauses": [],
                             "rejected": [], "unlocated": []})
            print(f"skip   {citation}: {why}", flush=True)
            return

        answer = lib.ask(
            "disputes", case["id"],
            citation=citation, opinion=lib.numbered(opinion),
            contracts=contracts,
            taxonomy=lib.taxonomy_lines(),
            risks=lib.risk_lines(sorted(case["keys"])),
            headnotes="\n".join(f"- [{k}] {h}"
                                for k, hs in case["keys"].items() for h in hs))
        if answer is None:
            return

        # One entry per clause, even when the model listed a clause twice: the
        # issues are pooled and `issues_of` folds what is genuinely duplicate.
        # Listing a clause twice is a presentation slip, not a claim that there
        # are two clauses.
        pooled = defaultdict(list)
        order = []
        for d in answer["disputed"]:
            k = (d["contract_id"], d["clause_id"])
            if k not in pooled:
                order.append(k)
            pooled[k] += d["issues"]

        kept, rejected = [], []
        for cid, clause_id in order:
            record, why = check(cid, clause_id, pooled[(cid, clause_id)],
                                case, inventory, opinion_lines)
            if record is None:
                rejected.append({"contract_id": cid, "clause_id": clause_id,
                                 "reason": why})
            else:
                kept.append(record)

        kept.sort(key=lambda c: (c["contract_id"], c["lines"]))
        rejected.sort(key=lambda r: (r["contract_id"], r["clause_id"]))

        lib.write_shard(SHARDS, case["id"], citation,
                        {"case_desc": answer["case_desc"], "clauses": kept,
                         "rejected": rejected,
                         "unlocated": answer.get("unlocated", [])})

        out = [f"disputes {citation}  ({len(ids)} document(s), "
               f"{sum(len(inventory[c]['clauses']) for c in ids)} clauses listed)"]
        for r in rejected:
            out.append(f"  ! rejected {r['contract_id']}/{r['clause_id']}: "
                       f"{r['reason']}")
        for record in kept:
            out.append(f"  {record['clause_id']} {record['clause_name'][:44]} "
                       f"[{record['taxonomy']}] {len(record['issues'])} issue(s) "
                       f"from {record['contract_id']}")
            for i in record["issues"]:
                out.append(f"      [{i['risk_type']}] op "
                           f"{i['opinion_lines'][0]}-{i['opinion_lines'][1]}: "
                           f"{i['issue'][:92]}")
            for w in record["dropped_issues"]:
                out.append(f"      ! issue dropped: {w}")
        for u in answer.get("unlocated", []):
            out.append(f"  ~ unlocated: {u['description'][:80]}  ({u['why'][:60]})")
        if not kept:
            out.append(f"  no clause reported: {answer['case_desc']}")
        print("\n".join(out), flush=True)

    if todo:
        with cf.ThreadPoolExecutor(max_workers=args.parallel) as pool:
            futs = [pool.submit(one, cit, ids) for cit, ids in todo]
            for fut in cf.as_completed(futs):
                fut.result()

    done = lib.merge_shards(SHARDS, OUT)

    kept = [c for v in done.values() for c in v["clauses"]]
    rejected = sum(len(v["rejected"]) for v in done.values())
    unlocated = sum(len(v.get("unlocated", [])) for v in done.values())
    issues = sum(len(c["issues"]) for c in kept)
    print(f"{len(done)} cases | {len(kept)} clauses disputed | {issues} issues | "
          f"{rejected} rejected | {unlocated} unlocated | "
          f"{len({c['contract_id'] for c in kept})} contracts won")

    # The distribution is the point of the multi-issue design, so it is printed
    # rather than left to be worked out later: it is the recall denominator the
    # issue-level scoring could not previously know.
    spread = Counter(len(c["issues"]) for c in kept)
    if spread:
        print("  issues per clause: " + ", ".join(
            f"{n}x{spread[n]}" for n in sorted(spread)))
    dropped = sum(len(c["dropped_issues"]) for c in kept)
    if dropped:
        print(f"  {dropped} issue(s) dropped from otherwise kept clauses")


if __name__ == "__main__":
    main()

"""Step 3 — what each issue step 2 recorded needs in order to be seen (one call per case).

Step 2 decided which clauses were disputed and what the court construed in each.
This step revisits none of that. It answers one question per issue: **where does
the evidence for it live** — in the clause's own words, elsewhere in the same
contract, in another document filed in the case, or in something the record
never held.

The question exists because step 2 was given every contract of the case, and the
risk-detection experiment gives its model one. An issue the court found by
reading a memorandum against the declaration of trust that governs it is not
findable from the memorandum alone, so scoring a one-document reader against it
measures the harness rather than the reader. `case` marks exactly those, and
`external` marks the ones no amount of mounting can supply.

**It is additive.** Nothing here changes a label, a clause, a risk type or an
issue. `disputes.json` is read and not written, so the dataset this annotates is
the same dataset as before, and a consumer that ignores `issue_scope.json` gets
what it always got.

An issue is named by the position step 2 left it in — contract, clause, and its
index in that clause's issue list, which `step2_disputes.py` sorts. The call
itself uses short ids (`i001`, …) for the same reason step 2 answers in clause
ids: the model cannot invent a location it was not offered.

Input : output/cases.json, output/disputes.json, output/contracts.json,
        output/inventory.json, output/contracts/*.md, output/opinions/<id>.txt
Output: output/issue_scope.json  (resumable — re-runs only what is missing)

Usage:
    python src/step3_issue_scope.py [--case CITATION] [--parallel N]
"""
import argparse
import concurrent.futures as cf
from collections import Counter, defaultdict

import lib

OUT = lib.OUT / "issue_scope.json"
SHARDS = "issue_scope"      # output/issue_scope/<case_id>.json while running
SCOPES = ("clause", "contract", "case", "external")
CLAUSE_CAP = 3000           # the clause is quoted in full above; this is a copy
FENCE = "```"


def documents(ids, texts):
    """Every contract of the case in full, each between markers naming its id.

    No clause list: this step answers about issues, not clauses, and the ids it
    may use are the issue ids in the task. The contract_ids still have to be
    spelled exactly, because `needs` names one, so the markers repeat at both
    ends as they do in step 2.
    """
    return "\n\n\n".join(
        f"---------- CONTRACT {cid} START ----------\n"
        f"{texts[cid]}\n"
        f"---------- CONTRACT {cid} END ----------" for cid in ids)


def enumerate_issues(found):
    """Every issue of the case, in the order step 2 stored them.

    The order is what makes the record addressable later: `clauses` is sorted by
    (contract_id, lines) and each `issues` list by (risk_type, lines, issue), so
    the same disputes.json always yields the same i-numbers.
    """
    out = []
    for c in found["clauses"]:
        for n, i in enumerate(c["issues"]):
            out.append({"issue_id": f"i{len(out) + 1:03d}",
                        "contract_id": c["contract_id"],
                        "clause_id": c["clause_id"],
                        "clause_name": c["clause_name"],
                        "issue_index": n,
                        "risk_type": i["risk_type"],
                        "issue": i["issue"],
                        "opinion_lines": i["opinion_lines"],
                        "opinion_comment": i["opinion_comment"]})
    return out


def issue_block(issues, by_id):
    """The issues as the model sees them, each with the clause it was found in."""
    out = []
    for e in issues:
        text = by_id[(e["contract_id"], e["clause_id"])]["text"]
        clipped = (text if len(text) <= CLAUSE_CAP else
                   text[:CLAUSE_CAP] + f"\n   … [{len(text) - CLAUSE_CAP:,} more "
                   f"characters — the clause is shown in full in the contract above]")
        out.append(
            f"### {e['issue_id']}\n"
            f"- **contract:** {e['contract_id']}\n"
            f"- **clause:** {e['clause_id']} — {e['clause_name']}\n"
            f"- **risk type:** {e['risk_type']}\n"
            f"- **the defect step 2 recorded:** {e['issue']}\n\n"
            f"The clause, verbatim:\n\n{FENCE}\n{clipped}\n{FENCE}\n\n"
            f"The passage of the opinion it came from — **lines "
            f"{e['opinion_lines'][0]}-{e['opinion_lines'][1]}**, an extract; "
            f"the court's reasoning may run past it:\n\n{FENCE}\n"
            f"{e['opinion_comment']}\n{FENCE}")
    return "\n\n".join(out)


def check(reported, issues, ids):
    """Resolve one reported scope against the issues offered, or say why not.

    Two corrections rather than rejections, because both are the model being
    imprecise about a scope it has otherwise chosen sensibly:

    - `case` naming only the issue's OWN contract is `contract`. The document it
      needs is the one the clause is already in.
    - `case` naming nothing that exists in this case cannot be acted on — there
      is no document to mount — so it is recorded as `external`, which is what
      an unavailable document is.

    A scope outside the enum cannot arrive: the schema is an enum.
    """
    known = {e["issue_id"]: e for e in issues}
    seen, kept, why = set(), [], []
    for r in reported:
        iid = str(r.get("issue_id", "")).strip()
        if iid not in known:
            why.append(f"issue_id {iid!r} is not one of the {len(known)} offered")
            continue
        if iid in seen:
            why.append(f"issue_id {iid!r} answered twice")
            continue
        seen.add(iid)
        e = known[iid]
        scope = r["scope"]
        needs = [n.strip() for n in str(r.get("needs") or "").split(",") if n.strip()]
        if scope == "case":
            named = [n for n in needs if n in ids and n != e["contract_id"]]
            if not named and any(n == e["contract_id"] for n in needs):
                why.append(f"{iid}: scope 'case' naming only its own contract "
                           f"-> 'contract'")
                scope, needs = "contract", []
            elif not named:
                why.append(f"{iid}: scope 'case' names {needs or 'nothing'}, "
                           f"not a document of this case -> 'external'")
                scope, needs = "external", needs or ["unnamed document"]
            else:
                needs = named
        elif scope != "external":
            needs = []
        kept.append({"contract_id": e["contract_id"], "clause_id": e["clause_id"],
                     "issue_index": e["issue_index"], "risk_type": e["risk_type"],
                     "issue": e["issue"], "scope": scope,
                     "needs": ",".join(needs), "why": r.get("why", "")})
    for iid in known:
        if iid not in seen:
            why.append(f"issue_id {iid!r} was not answered")
    kept.sort(key=lambda r: (r["contract_id"], r["clause_id"], r["issue_index"]))
    return kept, why


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", help="one citation only")
    ap.add_argument("--parallel", type=int, default=4,
                    help="cases processed at once (default 4)")
    args = ap.parse_args()

    cases = lib.read_json(lib.OUT / "cases.json", {})
    disputes = lib.read_json(lib.OUT / "disputes.json", {})
    registry = lib.read_json(lib.OUT / "contracts.json", {})
    inventory = lib.read_json(lib.OUT / "inventory.json", {})
    done = {**(lib.read_json(OUT, {}) or {}), **lib.read_shards(SHARDS)}
    if not disputes:
        raise SystemExit("output/disputes.json is empty — run step 2 first")

    # The documents step 2 was shown, which is what "this case holds" has to
    # mean here: a contract it never saw could not have produced an issue, and
    # offering it now would let this step mark something reachable that the
    # annotation never reached for.
    by_case = defaultdict(list)
    for cid, inv in inventory.items():
        if inv["clauses"]:
            by_case[inv["citation"]].append(cid)

    by_id = {(cid, c["clause_id"]): c
             for cid, inv in inventory.items() for c in inv["clauses"]}

    todo = [cit for cit in sorted(disputes)
            if not (args.case and cit != args.case) and cit not in done
            and disputes[cit]["clauses"]]

    def one(citation):
        case = cases[citation]
        found = disputes[citation]
        issues = enumerate_issues(found)
        ids = sorted(by_case.get(citation, []))
        texts = {cid: (lib.ROOT / registry[cid]["file"]).read_text(encoding="utf-8")
                 for cid in ids}
        contracts = documents(ids, texts)
        block = issue_block(issues, by_id)
        # Numbered, and whole. Step 2 cut each issue a passage, but a court
        # states a dependency where it suits the argument and not always beside
        # the words it construed — an extract alone makes a cross-document
        # issue look self-contained, which biases every scope toward `clause`.
        opinion = lib.numbered(
            (lib.OPINIONS / f"{case['id']}.txt").read_text(encoding="utf-8"))

        why = lib.out_of_bounds(opinion + contracts + block)
        if why:
            lib.write_shard(SHARDS, case["id"], citation,
                            {"scopes": [], "warnings": [f"skipped: {why}"]})
            print(f"skip   {citation}: {why}", flush=True)
            return

        answer = lib.ask("issue_scope", case["id"], effort="high",
                         model=lib.MODEL, citation=citation, opinion=opinion,
                         contracts=contracts, issues=block)
        if answer is None:
            return

        kept, warnings = check(answer["scopes"], issues, set(ids))
        lib.write_shard(SHARDS, case["id"], citation,
                        {"scopes": kept, "warnings": warnings})

        spread = Counter(r["scope"] for r in kept)
        out = [f"scope {citation}  ({len(ids)} document(s), {len(kept)} issue(s): "
               + ", ".join(f"{s} {spread[s]}" for s in SCOPES if spread[s]) + ")"]
        for r in kept:
            if r["scope"] in ("case", "external"):
                out.append(f"  {r['clause_id']} of {r['contract_id']} "
                           f"[{r['risk_type']}] -> {r['scope']}"
                           + (f" ({r['needs']})" if r["needs"] else ""))
                out.append(f"      {r['why'][:100]}")
        for w in warnings:
            out.append(f"  ! {w}")
        print("\n".join(out), flush=True)

    if todo:
        with cf.ThreadPoolExecutor(max_workers=args.parallel) as pool:
            futs = [pool.submit(one, cit) for cit in todo]
            for fut in cf.as_completed(futs):
                fut.result()

    done = lib.merge_shards(SHARDS, OUT)
    rows = [r for v in done.values() for r in v["scopes"]]
    warned = sum(len(v.get("warnings", [])) for v in done.values())
    print(f"\n{len(done)} cases | {len(rows)} issues scoped | {warned} warning(s)")

    # The distribution is the reason the step exists: `case` is what mounting a
    # contract's siblings can reach, `external` is what nothing can.
    for code, label in (("1", "risk type 1"), ("2", "risk type 2")):
        sub = [r for r in rows if r["risk_type"].startswith(code)]
        if not sub:
            continue
        spread = Counter(r["scope"] for r in sub)
        print(f"  {label} ({len(sub)}): " + "  ".join(
            f"{s} {spread[s]} ({100 * spread[s] / len(sub):.0f}%)"
            for s in SCOPES if spread[s]))
    ext = [r for r in rows if r["scope"] == "external"]
    if ext:
        print(f"  {len(ext)} issue(s) need a document the corpus does not hold")


if __name__ == "__main__":
    main()

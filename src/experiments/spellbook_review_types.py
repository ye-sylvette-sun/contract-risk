"""Classify Spellbook's issues by risk type, and restate the typed ones in the dataset's form.

One call per contract (`prompts/spellbook_issue_types.md`). Every located
Risks & Negotiation issue from both parties goes up with the contract, and
comes back as `type1`, `type2` or `none` with a sub-code and a reason — and,
for the typed ones, an `issue` sentence: the tool's own finding restated the
way `dataset.csv` records a defect, so the alignment judge compares like with
like. The restatement is bound to the tool's text: it may change the register,
not the content.

The review pass in `spellbook/risks_negotiation/review.py` classified these
once already, without the restatement. This replaces that classification for
everything downstream; the review pass's locations are still what put each
issue in a clause.

Input : spellbook/risks_negotiation/results/<cid>.json, output/contracts/<cid>.md,
        output/dataset.csv
Output: output/spellbook_review_types.json  (resumable — re-runs only what is missing)

Usage:
    python src/experiments/spellbook_review_types.py [--parallel N] [--contract CID]
"""
import argparse
import concurrent.futures as cf
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402
import runs  # noqa: E402
from spellbook_review_preds import RESULTS, DATASET, TAB, located  # noqa: E402

OUT = lib.OUT / "spellbook_review_types.json"
SHARDS = "spellbook_review_types"


def block(items):
    """The numbered issue list sent up. `ref` indexes `items`."""
    out = []
    for ref, (party, it, clause) in enumerate(items):
        lines = [f"[{ref}] raised for: {party}",
                 f"    title: {it['title']}"]
        if it.get("description"):
            lines.append(f"    the tool's reasoning: {it['description']}")
        if it.get("comment"):
            lines.append(f"    the tool's comment on its edit: {it['comment']}")
        lines.append(f"    clause it lands in: {clause['clause_id']} "
                     f"({clause['clause_name']})")
        lines.append(f"    text matched: {it['location']['matched_text']}")
        out.append("\n".join(lines))
    return "\n\n".join(out)


def one(cid, clauses):
    items = list(located(cid, clauses, TAB))
    if not items:
        return {}
    doc = (lib.OUT / "contracts" / f"{cid}.md").read_text(encoding="utf-8")
    a = lib.ask("spellbook_issue_types", cid, effort="high",
                citation=clauses[0]["citation"], contract_id=cid, document=doc,
                taxonomy=lib.taxonomy_lines(), issues=block(items),
                n_issues=len(items))
    if a is None:
        return None
    by_ref = {j["ref"]: j for j in a.get("judgments", []) if isinstance(j.get("ref"), int)}
    out = {}
    for ref, (party, it, clause) in enumerate(items):
        j = by_ref.get(ref)
        if j is None:
            continue
        # Keyed the way the review file is: party, then the issue's index there.
        out[f"{party}/{it['index']}"] = {
            "type": j["type"], "subtype": j["subtype"],
            "issue": j["issue"].strip(), "reason": j["reason"],
            "clause_id": clause["clause_id"],
        }
    missing = len(items) - len(out)
    if missing:
        print(f"  {cid}: {missing} of {len(items)} issue(s) came back without a judgment")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--contract")
    args = ap.parse_args()

    dataset = runs.by_contract(runs.load_rows(DATASET))
    done = {**(lib.read_json(OUT, {}) or {}), **lib.read_shards(SHARDS)}
    todo = [p.stem for p in sorted(RESULTS.glob("*.json"))
            if p.stem in dataset and p.stem not in done
            and (not args.contract or p.stem == args.contract)]
    print(f"{len(done)} contract(s) done, {len(todo)} to run ({lib.MODEL}, "
          f"{args.parallel} at a time)")

    def run(cid):
        r = one(cid, dataset[cid])
        if r is None:
            print(f"  {cid}: no answer", flush=True)
            return
        lib.write_shard(SHARDS, cid, cid, r)
        n = sum(1 for v in r.values() if v["type"] != "none")
        print(f"  {cid}: {len(r)} issues, {n} typed", flush=True)

    with cf.ThreadPoolExecutor(max_workers=args.parallel) as pool:
        for f in cf.as_completed([pool.submit(run, c) for c in todo]):
            f.result()
    res = lib.merge_shards(SHARDS, OUT)
    typed = sum(1 for r in res.values() for v in r.values() if v["type"] != "none")
    total = sum(len(r) for r in res.values())
    print(f"\n{len(res)} contracts, {total} issues, {typed} typed -> {OUT}")


if __name__ == "__main__":
    main()

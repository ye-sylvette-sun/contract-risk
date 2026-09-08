"""Does the detection prompt's own type test reproduce the recorded types?

The agent finds the right provision, names the defect the court construed, and
then files it under the other risk type from the one the data records. Either it
is applying our test badly, or our test does not agree with the types the data
carries. Only one of those is worth spending an agent run on, and this tells
them apart for the price of one pass over the labels.

Every distinct gold type-2 issue, plus an equal number of distinct type-1 issues
as a **control**. The control is what makes the result readable: without it, a
judge that simply prefers answering `1` cannot be told apart from a rule that
really does yield `1` on the type-2 cases. It doubles as a noise check — random
flips in the control mean the judge is unstable and the run says nothing.

Blind: the recorded type is never shown, and the prompt says not to guess it.
One call per contract, so the contract is sent once and every issue found in it
is judged with the other provisions in view. Resumable via shards.

The rule under test is quoted verbatim in `prompts/type_test.md`. To test a
different wording, change the quote there and re-run -- moving `output/type_test/`
aside first, or the shards make it a no-op.

Artefacts:

    output/type_test/              one shard per contract
    output/type_test.json          verdicts and the issue metadata behind them
    output/llm_logs/type_test/     request, response and usage per call

Usage:
    python src/experiments/type_test_audit.py
    python src/experiments/type_test_audit.py --report
"""
import collections
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib  # noqa: E402
import runs as api  # noqa: E402

SHARDS = "type_test"
OUT = lib.OUT / "type_test.json"
# Enough of a provision to judge the defect by; the whole contract is in the
# document section anyway, so nothing is actually hidden by the cap.
CLAUSE_CAP = 3000


def collect():
    """(issues, by_contract) — distinct gold issues, deduped on issue text.

    Deduped because one recorded defect can sit on several provisions (a term
    construed once, repeated in three clauses), and counting it three times
    would weight the audit by how often a drafter copied a paragraph.
    """
    rows = api.load_rows(lib.OUT / "dataset.csv")
    first = {}
    for r in rows:
        for i in json.loads(r["issues"]):
            t = i["issue"].strip()
            if t and t not in first:
                first[t] = (r, i)
    t2 = [t for t, (_, i) in first.items() if i["risk_type"].startswith("2")]
    t1 = [t for t, (_, i) in first.items() if i["risk_type"].startswith("1")]
    random.Random(11).shuffle(t1)
    picked = t2 + t1[:len(t2)]
    random.Random(12).shuffle(picked)        # no type ordering anywhere
    issues = []
    for n, t in enumerate(picked, 1):
        r, i = first[t]
        issues.append({"issue_id": f"i{n:03d}", "text": t,
                       "gold": 2 if i["risk_type"].startswith("2") else 1,
                       "subtype": i["risk_type"], "contract_id": r["contract_id"],
                       "clause_id": r["clause_id"], "clause_name": r["clause_name"],
                       "clause_text": r["clause_text"]})
    by = collections.defaultdict(list)
    for it in issues:
        by[it["contract_id"]].append(it)
    return issues, by


def block(items):
    out = []
    for it in items:
        txt = it["clause_text"]
        if len(txt) > CLAUSE_CAP:
            txt = txt[:CLAUSE_CAP] + "\n[...provision truncated...]"
        out.append(f"--- ISSUE {it['issue_id']} ---\n"
                   f"found in provision [{it['clause_id']}] {it['clause_name']}\n\n"
                   f"{txt}\n\nrecorded defect: {it['text']}\n")
    return "\n".join(out)


def check(answer, items):
    want = {it["issue_id"] for it in items}
    got = {v["issue_id"] for v in (answer or {}).get("verdicts", [])}
    if want - got:
        print(f"  ! missing {len(want - got)} verdict(s)")
    return [v for v in (answer or {}).get("verdicts", []) if v["issue_id"] in want]


def report():
    issues, _ = collect()
    meta = {it["issue_id"]: it for it in issues}
    got = {}
    for _, v in lib.read_shards(SHARDS).items():
        for x in v:
            got[x["issue_id"]] = x
    lib.write_json(OUT, {"verdicts": [got[k] for k in sorted(got)],
                         "meta": {k: meta[k] for k in sorted(got)}}, quiet=True)
    print(f"\n{len(got)} of {len(issues)} issues judged\n")
    if not got:
        return

    print("=" * 72)
    print("DOES OUR OWN TYPE TEST REPRODUCE THE RECORDED TYPE?")
    print("=" * 72)
    print(f"  {'recorded':<12}{'n':>5}{'rule says 1':>13}{'rule says 2':>13}{'agrees':>9}")
    bal = []
    for g in (1, 2):
        v = [got[k] for k in got if meta[k]["gold"] == g]
        if not v:
            continue
        a = sum(1 for x in v if x["type"] == g)
        bal.append(a / len(v))
        print(f"  type {g:<7}{len(v):>5}{sum(1 for x in v if x['type']==1):>13}"
              f"{sum(1 for x in v if x['type']==2):>13}{100*a/len(v):>8.0f}%")
    if len(bal) == 2:
        print(f"\n  balanced accuracy {50*sum(bal):.0f}%")

    print()
    print("=" * 72)
    print("HOW CLEARLY DOES THE RULE DECIDE, AND DOES IT AGREE WHEN IT IS CLEAR?")
    print("=" * 72)
    for g in (1, 2):
        v = [got[k] for k in got if meta[k]["gold"] == g]
        if not v:
            continue
        print(f"  recorded type {g}:")
        for c in ("clear", "arguable", "underdetermined"):
            s = [x for x in v if x["clarity"] == c]
            if not s:
                continue
            a = sum(1 for x in s if x["type"] == g)
            print(f"    {c:<17}{len(s):>4} ({100*len(s)/len(v):>3.0f}%)   "
                  f"agrees {a}/{len(s)} ({100*a/len(s):>3.0f}%)")

    print()
    print("=" * 72)
    print("WHERE THE RULE DISAGREES WITH THE RECORDED TYPE 2")
    print("=" * 72)
    bad = [k for k in sorted(got) if meta[k]["gold"] == 2 and got[k]["type"] == 1]
    print(f"  {len(bad)} of the recorded type-2 issues, the rule calls type 1\n")
    for k in bad:
        m, x = meta[k], got[k]
        print(f"  {k} [{x['clarity']}] {m['contract_id'][:38]}/{m['clause_id']}")
        print(f"    GOLD 2 : {m['text'][:150]}")
        print(f"    rewrite: {x['rewrite'][:150]}")
        if (x.get("note") or "").strip():
            print(f"    note   : {x['note'][:170]}")
        print()


def main():
    if "--report" in sys.argv:
        return report()
    issues, by = collect()
    print(f"{len(issues)} distinct issues "
          f"({sum(1 for i in issues if i['gold']==2)} recorded type-2, "
          f"{sum(1 for i in issues if i['gold']==1)} type-1 control) "
          f"over {len(by)} contracts")
    done = set(lib.read_shards(SHARDS))
    todo = [c for c in sorted(by) if c not in done]
    print(f"{len(done)} contract(s) done, {len(todo)} to run "
          f"({lib.MODEL}, effort high)\n")
    reg = lib.read_json(lib.OUT / "contracts.json", {})
    for n, cid in enumerate(todo, 1):
        items = by[cid]
        doc = (lib.ROOT / reg[cid]["file"]).read_text(encoding="utf-8",
                                                      errors="replace")
        print(f"[{n}/{len(todo)}] {cid}: {len(items)} issue(s), {len(doc):,} chars")
        answer = lib.ask("type_test", cid, effort="high", model=lib.MODEL,
                         contract_id=cid, document=doc, issues=block(items))
        v = check(answer, items)
        if v:
            lib.write_shard(SHARDS, f"{cid}.json", cid, v)
    report()


if __name__ == "__main__":
    main()

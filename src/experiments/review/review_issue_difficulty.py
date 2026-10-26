"""How hard is each alignment call to check? One call per case, one rating per call.

The alignment judge said, for every issue the risk-detection agent named on a
provision the court construed, which of that provision's court defects the
issue names, or that it names none. A reviewer checks those calls. This asks a
model how hard each check is for someone who **already understands the case** —
the case's own difficulty is `review_case_difficulty.py`'s, and is not counted
again here.

**The model is shown the judge's call and nothing else of the judge's.** Which
court defect it matched, or that it matched none, is what the reviewer checks,
so it is the item. The alignment score, the judge's reasoning and the agent's
probability are not shown: the difficulty is to be compared against the score,
so it must not be built from it. The score is copied into the result for that
analysis, after the call.

A case none of whose provisions carries a judged issue has nothing to rate and
is skipped.

Input : output/dataset/{dataset.csv,cases.json,contracts.json,contracts/,opinions/},
        output/issue_alignment/issue_alignment_check.json
Output: output/review/review_issue_difficulty.json  (resumable — re-runs only what is missing)
        output/review/llm_logs/review_issue_difficulty/<case_id>.json

Usage:
    python src/experiments/review/review_issue_difficulty.py --dry-run     # sizes, no calls
    python src/experiments/review/review_issue_difficulty.py --case "122 F.Supp.3d 1157" --out DIR
    python src/experiments/review/review_issue_difficulty.py [--case CITATION] [--parallel N]
"""
import argparse
import concurrent.futures as cf
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # src/: lib
sys.path.insert(0, str(Path(__file__).resolve().parent))       # review/: the case run
import lib  # noqa: E402
from review_case_difficulty import (  # noqa: E402
    DIFFICULTY, FENCE, MODEL, EFFORT, eval_cases, logged, material)

PROMPT = "review_issue_difficulty"
ALIGNMENT = lib.OUT / "issue_alignment" / "issue_alignment_check.json"


def items(judged, keys):
    """The calls of one case to rate, ids a01, a02, ... in job order.

    Each shows the provision, its court defects by id, the agent's description
    and the judge's call — and nothing else the judge wrote.
    """
    out, meta = [], {}
    for j in sorted(judged, key=lambda j: j["job_id"]):
        aid = f"a{len(meta) + 1:02d}"
        cands = [keys[c["key"]] for c in j["candidates"]]
        hit = keys[j["matched_key"]] if j["matched_key"] else ""
        call = (f"The first reader matched it to **{hit}**." if hit else
                "The first reader matched it to **none** of "
                + ", ".join(cands) + ".")
        out += [f"#### {aid}", "",
                f"On `{j['contract_id']}` {j['clause_id']} — {j['clause_name']}. "
                f"Court defects on this provision: {', '.join(cands)}.", "",
                "The description:", f"{FENCE}\n{j['issue']}\n{FENCE}", "",
                call, ""]
        meta[aid] = {"job_id": j["job_id"], "call": "match" if hit else "none",
                     "matched": hit, "candidates": cands,
                     "alignment": j["alignment"]}
    return "\n".join(out), meta


def check(entry):
    """Warnings about one entry that is well-formed but inconsistent."""
    s, d = entry.get("score"), entry.get("difficulty")
    if not isinstance(s, int) or not 1 <= s <= 10:
        return [f"score {s!r} is not an integer 1-10"]
    if d in DIFFICULTY and s not in DIFFICULTY[d]:
        return [f"score {s} does not agree with difficulty {d!r}"]
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", action="append", metavar="CITATION",
                    help="one citation; repeat for several")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--effort", default=EFFORT)
    ap.add_argument("--out", help="directory for a trial run's result and logs, "
                                  "instead of output/review/")
    ap.add_argument("--dry-run", action="store_true",
                    help="build every packet and print its size; no calls")
    args = ap.parse_args()

    stage = Path(args.out) if args.out else lib.REVIEW_OUT
    result = stage / f"{PROMPT}.json"
    log_dir = stage / "llm_logs" / PROMPT

    by_case, cases, registry = eval_cases()
    judged = defaultdict(list)
    for j in lib.read_json(ALIGNMENT, {}).values():
        judged[j["citation"]].append(j)
    done = lib.read_json(result, {}) or {}

    if args.case:
        unknown = [c for c in args.case if c not in by_case]
        if unknown:
            raise SystemExit(f"not in the evaluation set: {unknown}")
    chosen = [c for c in sorted(by_case) if not args.case or c in args.case]
    empty = [c for c in chosen if not judged[c]]
    todo = [c for c in chosen if c not in empty and (args.dry_run or c not in done)]
    if empty:
        print(f"{len(empty)} case(s) skipped, no judged issue: {', '.join(empty)}")

    def build(citation):
        opinion, contracts, pk, _, keys = material(by_case[citation], cases, registry)
        text, meta = items(judged[citation], keys)
        return opinion, contracts, pk, text, meta

    if args.dry_run:
        total = n = 0
        for c in todo:
            opinion, contracts, pk, text, meta = build(c)
            t = lib.n_tokens(opinion + contracts + pk + text)
            total += t; n += len(meta)
            print(f"{c:24s} {len(meta):3d} item(s)  {t:>8,} tokens")
        print(f"\n{len(todo)} case(s), {n} item(s), {total:,} input tokens "
              f"before the prompt's own text")
        return

    def one(citation):
        opinion, contracts, pk, text, meta = build(citation)
        why = lib.out_of_bounds(opinion + contracts + pk + text)
        if why:
            print(f"skip   {citation}: {why}", flush=True)
            return citation, None
        fields = dict(citation=citation, opinion=opinion, contracts=contracts,
                      packet=pk, items=text)
        answer = logged(log_dir, PROMPT, args.model, cases[citation]["id"], fields)
        if answer is not None:
            print(f"from log  {citation}", flush=True)
        else:
            answer = lib.ask(PROMPT, cases[citation]["id"], effort=args.effort,
                             model=args.model, log_dir=log_dir, **fields)
        if answer is None:
            return citation, None
        got = Counter(e.get("id") for e in answer["items"])
        warnings = ([f"{a} not rated" for a in meta if not got[a]]
                    + [f"{a} rated {k} times" for a, k in got.items() if k > 1]
                    + [f"{a} is not an item shown" for a in got if a not in meta])
        rated = {}
        for e in answer["items"]:
            if e.get("id") in meta and e["id"] not in rated:
                rated[e["id"]] = {**meta[e["id"]], **e, "warnings": check(e)}
                warnings += [f"{e['id']}: {w}" for w in rated[e["id"]]["warnings"]]
        return citation, {"items": rated, "warnings": warnings}

    failed = []
    if todo:
        with cf.ThreadPoolExecutor(max_workers=args.parallel) as pool:
            futs = {pool.submit(one, c): c for c in todo}
            for fut in cf.as_completed(futs):
                # One case failing — a dropped stream, an exhausted account —
                # must not throw away the answers other workers already have.
                try:
                    citation, answer = fut.result()
                except Exception as e:                       # noqa: BLE001
                    failed.append(futs[fut])
                    print(f"  ! {futs[fut]}: {type(e).__name__}: "
                          f"{str(e)[:160]}", flush=True)
                    continue
                if answer is None:
                    failed.append(citation)
                    continue
                done[citation] = answer
                lib.write_json(result, done, quiet=True)
                spread = Counter(v["difficulty"] for v in answer["items"].values())
                print(f"{citation:24s} {len(answer['items']):3d} item(s)  " + ", ".join(
                    f"{d} {spread[d]}" for d in DIFFICULTY if spread[d]), flush=True)
                for w in answer["warnings"]:
                    print(f"  ! {w}", flush=True)

    spread = Counter(v["difficulty"] for c in done.values() for v in c["items"].values())
    print(f"\n{len(done)} case(s), {sum(spread.values())} item(s): " + ", ".join(
        f"{d} {spread[d]}" for d in DIFFICULTY if spread[d]))
    if failed:
        raise SystemExit(f"{len(failed)} case(s) failed and are not in the "
                         f"result — re-run to resume: {sorted(failed)}")


if __name__ == "__main__":
    main()

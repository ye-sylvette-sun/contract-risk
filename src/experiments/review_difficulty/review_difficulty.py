"""How hard is each case to understand? One call per case.

A human reviewer checks whether descriptions of defects match the defects a
court construed, and the first reviews ran from ten minutes to three hours a
case — most of it spent understanding the case and what the court construed.
This asks a model, before anyone starts, which cases are which: `easy`,
`intermediate` or `hard`, a 1-10 score, a time estimate, and the factors
behind it.

**The model is shown the case and the court's defects, and nothing else.** The
whole opinion, every contract of the case, and a packet listing every provision
the court construed with every defect the court construed in it and the passage
it comes from. No risk-detection predictions and no alignment judge's calls, so
the difficulty is a property of the case and can be compared against anything a
model later did on it without having been built from it.

**The number of court defects is not difficulty**, and the prompt says so: one
hard defect makes a case hard, many plain ones leave it easy.

The evaluation set is the risk-detection one — every case except those of the
worked examples (`runs.held_out`) — computed from the dataset alone. A case
with no court defect in it has nothing to understand and is skipped.

The difficulty factors in the prompt come from a reviewer's account of her
first reviews, and describe them without naming the cases she reviewed, so
those cases (122 F.Supp.3d 1157, hard; 787 F.Supp.2d 118, easy) can test it.

Input : output/dataset/dataset.csv, output/dataset/cases.json,
        output/dataset/contracts.json, output/dataset/contracts/*.md,
        output/dataset/opinions/<id>.txt
Output: output/review_difficulty/review_difficulty.json  (resumable — re-runs only what is missing)
        output/review_difficulty/llm_logs/review_difficulty/<case_id>.json

Usage:
    python src/experiments/review_difficulty/review_difficulty.py --dry-run     # sizes, no calls
    python src/experiments/review_difficulty/review_difficulty.py --case "122 F.Supp.3d 1157" --out DIR
    python src/experiments/review_difficulty/review_difficulty.py [--case CITATION] [--parallel N]
"""
import argparse
import concurrent.futures as cf
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # src/: lib
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # experiments/: runs
import lib  # noqa: E402
import runs  # noqa: E402

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

MODEL = "gpt-6.1-sol"
EFFORT = "medium"
PROMPT = "review_difficulty"
DATASET = lib.DATASET_OUT / "dataset.csv"
CLAUSE_CAP = 3000           # a construed clause; the contract above has it whole
FENCE = "```"
DIFFICULTY = {"easy": range(1, 4), "intermediate": range(4, 7), "hard": range(7, 11)}


def documents(ids, texts):
    """Every contract of the case in full, between markers naming its id."""
    return "\n\n\n".join(
        f"---------- CONTRACT {cid} START ----------\n"
        f"{texts[cid]}\n"
        f"---------- CONTRACT {cid} END ----------" for cid in ids)


def clip(text, cap):
    return text if len(text) <= cap else (
        text[:cap] + f"\n… [{len(text) - cap:,} more characters — the clause is "
        f"in full in the contract above]")


def packet(rows):
    """The court defects of one case, and their ids.

    Contract by contract, every construed provision with every defect recorded
    on it. Defects are numbered d01, d02, ... across the case. A passage already
    printed for an earlier defect is referred to rather than repeated: that two
    defects share the court's words is itself something the reader has to work
    through, so it is said, not hidden.
    """
    out, ids, seen = [], [], {}
    by_contract = defaultdict(list)
    for r in rows:
        by_contract[r["contract_id"]].append(r)
    for cid in sorted(by_contract):
        construed = sorted((r for r in by_contract[cid] if r["label"] == "POSITIVE"),
                           key=lambda r: int(r["source_span"].split("-")[0]))
        out += [f"### Contract {cid}", ""]
        if not construed:
            out += ["The court construed no provision of this contract.", ""]
            continue
        for r in construed:
            golds = json.loads(r["issues"] or "[]")
            out += [f"#### {r['clause_id']} — {r['clause_name']}", "",
                    f"{FENCE}\n{clip(r['clause_text'], CLAUSE_CAP)}\n{FENCE}", ""]
            for g in golds:
                did = f"d{len(ids) + 1:02d}"
                ids.append(did)
                needs = f" — needs {g['scope_needs']}" if g.get("scope_needs") else ""
                lines = g.get("opinion_lines") or ["?", "?"]
                passage = (g.get("opinion_comment") or "").strip()
                out += [f"- **{did}** (risk type {g['risk_type']}, scope "
                        f"`{g.get('scope', '')}`{needs}): {g['issue']}",
                        f"  Opinion lines {lines[0]}-{lines[1]}:"]
                if passage in seen:
                    out.append(f"  The same passage as {seen[passage]} above.")
                else:
                    seen[passage] = did
                    out.append(f"{FENCE}\n{passage}\n{FENCE}")
                out.append("")
    return "\n".join(out), ids


def check(answer, ids):
    """Warnings about an answer that is well-formed but inconsistent."""
    why = []
    s, d = answer.get("score"), answer.get("difficulty")
    if not isinstance(s, int) or not 1 <= s <= 10:
        why.append(f"score {s!r} is not an integer 1-10")
    elif d in DIFFICULTY and s not in DIFFICULTY[d]:
        why.append(f"score {s} does not agree with difficulty {d!r}")
    named = (answer.get("hardest_defect") or "").split()[:1]
    if ids and not (named and named[0].strip(".,:;—-") in ids):
        why.append(f"hardest_defect does not start with one of {ids[0]}..{ids[-1]}")
    return why


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", action="append", metavar="CITATION",
                    help="one citation; repeat for several")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--effort", default=EFFORT)
    ap.add_argument("--out", help="directory for a trial run's result and logs, "
                                  "instead of output/review_difficulty/")
    ap.add_argument("--dry-run", action="store_true",
                    help="build every packet and print its size; no calls")
    args = ap.parse_args()

    stage = Path(args.out) if args.out else lib.DIFFICULTY_OUT
    result = stage / "review_difficulty.json"
    log_dir = stage / "llm_logs" / PROMPT

    cases = lib.read_json(lib.DATASET_OUT / "cases.json", {})
    registry = lib.read_json(lib.DATASET_OUT / "contracts.json", {})
    rows = runs.load_rows(DATASET)
    taught = runs.held_out(rows, runs.load_examples(rows))
    by_case = defaultdict(list)
    for r in rows:
        if r["contract_id"] not in taught:
            by_case[r["citation"]].append(r)
    done = lib.read_json(result, {}) or {}

    if args.case:
        unknown = [c for c in args.case if c not in by_case]
        if unknown:
            raise SystemExit(f"not in the evaluation set: {unknown}")
    chosen = [c for c in sorted(by_case) if not args.case or c in args.case]
    empty = [c for c in chosen
             if not any(r["label"] == "POSITIVE" for r in by_case[c])]
    todo = [c for c in chosen if c not in empty and (args.dry_run or c not in done)]
    if empty:
        print(f"{len(empty)} case(s) skipped, no court defect: {', '.join(empty)}")

    def material(citation):
        crow = by_case[citation]
        ids = sorted({r["contract_id"] for r in crow})
        texts = {cid: (lib.ROOT / registry[cid]["file"]).read_text(encoding="utf-8")
                 for cid in ids}
        opinion = lib.numbered((lib.OPINIONS / f"{cases[citation]['id']}.txt")
                               .read_text(encoding="utf-8"))
        pk, defect_ids = packet(crow)
        return opinion, documents(ids, texts), pk, defect_ids

    if args.dry_run:
        total = 0
        for c in todo:
            opinion, contracts, pk, defect_ids = material(c)
            t = lib.n_tokens(opinion + contracts + pk)
            total += t
            print(f"{c:24s} {len(defect_ids):3d} court defect(s)  {t:>8,} tokens")
        print(f"\n{len(todo)} case(s), {total:,} input tokens before the prompt's own text")
        return

    def logged(cid, fields):
        """The answer already in this case's log, if that log is this exact call.

        A call that was answered and logged but never reached the result file —
        the run died on another worker first — is read back rather than billed
        again. Only when the log is the same request: same model, same prompt
        sections, same document by digest.
        """
        path = log_dir / f"{cid}.json"
        if not path.exists():
            return None
        log = json.loads(path.read_text(encoding="utf-8"))
        p = lib.prompt(PROMPT, **fields)
        same = (log.get("ok") and str(log.get("model", "")).startswith(args.model)
                and log["system"] == p["SYSTEM"]
                and log["instructions"] == p["INSTRUCTIONS"]
                and log["task"] == p["TASK"]
                and log["document_sha256"] == hashlib.sha256(
                    p["DOCUMENT"].encode("utf-8")).hexdigest())
        if not same:
            return None
        text = "".join(c.get("text", "") for b in log["response"]
                       if b.get("type") == "message"
                       for c in b.get("content") or []
                       if c.get("type") == "output_text")
        return json.loads(text) if text else None

    def one(citation):
        opinion, contracts, pk, defect_ids = material(citation)
        why = lib.out_of_bounds(opinion + contracts + pk)
        if why:
            print(f"skip   {citation}: {why}", flush=True)
            return citation, None
        fields = dict(citation=citation, opinion=opinion, contracts=contracts,
                      packet=pk)
        answer = logged(cases[citation]["id"], fields)
        if answer is not None:
            print(f"from log  {citation}", flush=True)
        else:
            answer = lib.ask(PROMPT, cases[citation]["id"], effort=args.effort,
                             model=args.model, log_dir=log_dir, **fields)
        if answer is None:
            return citation, None
        answer["court_defects"] = len(defect_ids)
        answer["warnings"] = check(answer, defect_ids)
        return citation, answer

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
                print(f"{answer['difficulty']:12s} {answer['score']:2d}  "
                      f"~{answer['estimated_minutes']} min  {citation}  "
                      f"({answer['court_defects']} court defect(s))", flush=True)
                for w in answer["warnings"]:
                    print(f"  ! {w}", flush=True)

    spread = Counter(v["difficulty"] for v in done.values())
    print(f"\n{len(done)} case(s): " + ", ".join(
        f"{d} {spread[d]}" for d in DIFFICULTY if spread[d]))
    if failed:
        raise SystemExit(f"{len(failed)} case(s) failed and are not in the "
                         f"result — re-run to resume: {sorted(failed)}")


if __name__ == "__main__":
    main()

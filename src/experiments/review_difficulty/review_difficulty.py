"""How hard is each case for a human to review? One call per case.

The alignment judge's calls have to be checked by a person, and the first
reviews ran from ten minutes to three hours a case. This asks a model, before
anyone starts, which cases are which: `easy`, `intermediate` or `hard`, a 1-10
score, a time estimate, and the factors behind it.

**What the model is shown is what the reviewer is shown.** The whole opinion,
every contract of the case, and a packet listing, contract by contract:

- every provision the court construed, with each recorded defect and its
  passage, and under it every issue the agent named there with the judge's call
  on it — these are the **judged items** the reviewer decides;
- a construed provision the agent said nothing about, as a miss;
- every issue the agent named on a provision no court construed, as context,
  since the agent can find a relational defect on the neighbouring provision.

**The judge's numeric alignment score is not shown**, only its call (which
recorded defect, or none) and its reasoning. Whether difficulty tracks that
score is the question this output is meant to answer, so the score must not be
an input to it.

The difficulty factors in the prompt come from a reviewer's account of her
first reviews, and describe them without naming the cases she reviewed, so
those cases can test the prompt.

Input : output/risk_detect/risk_detect_agent_preds.csv, output/dataset/dataset.csv,
        output/issue_alignment/issue_alignment_check.json, output/dataset/cases.json,
        output/dataset/contracts.json, output/dataset/contracts/*.md,
        output/dataset/opinions/<id>.txt
Output: output/review_difficulty/review_difficulty.json  (resumable — re-runs only what is missing)

Usage:
    python src/experiments/review_difficulty/review_difficulty.py [--case CITATION] [--parallel N]
    python src/experiments/review_difficulty/review_difficulty.py --case "122 F.Supp.3d 1157" --out DIR
    python src/experiments/review_difficulty/review_difficulty.py --dry-run     # packet sizes, no calls
"""
import argparse
import concurrent.futures as cf
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import lib  # noqa: E402

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

MODEL = "gpt-6.1-sol"
EFFORT = "medium"
PROMPT = "review_difficulty"
PREDS = lib.RISK_DETECT_OUT / "risk_detect_agent_preds.csv"
DATASET = lib.DATASET_OUT / "dataset.csv"
ALIGNMENT = lib.ALIGNMENT_OUT / "issue_alignment_check.json"
THRESHOLD = 0.5             # the judge's call, as the report applies it
CLAUSE_CAP = 3000           # a construed clause; the contract above has it whole
LOCATOR = 300               # an unconstrued clause: enough to find it above
FENCE = "```"
LEVELS = ("low", "medium", "high")
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


def job_ids(r):
    """The alignment check's job id for each issue of one prediction row.

    The same rule as `issue_alignment_check.jobs`: numbered per type, in list
    order, so the n-th type-1 issue of a provision is `__t1_<n>`.
    """
    seen = {1: 0, 2: 0}
    out = []
    for it in json.loads(r["issues"]):
        t = it.get("type")
        if not it.get("issue") or t not in (1, 2):
            out.append((it, None))
            continue
        seen[t] += 1
        out.append((it, f"{r['contract_id']}__{r['clause_id']}__t{t}_{seen[t]}"))
    return out


def judge_call(a):
    """The judge's call in words, without its numeric score."""
    if a is None:
        return "not judged"
    if a.get("matched") and float(a.get("alignment") or 0) >= THRESHOLD:
        return f"names recorded defect {a['matched']}"
    return "names none of the recorded defects"


def construed_block(r, src, align, counter):
    """One construed provision: its defects, then every judged item on it."""
    golds = [g for g in json.loads(src["issues"] or "[]")]
    out = [f"#### {r['clause_id']} — {src['clause_name']}  (construed by the court)",
           "", f"{FENCE}\n{clip(src['clause_text'], CLAUSE_CAP)}\n{FENCE}", "",
           f"The court construed **{len(golds)}** defect(s) here:"]
    for n, g in enumerate(golds, 1):
        scope = g.get("scope", "")
        needs = f" — needs {g['scope_needs']}" if g.get("scope_needs") else ""
        lines = g.get("opinion_lines") or ["?", "?"]
        out += ["", f"- **g{n}** (risk type {g['risk_type']}, scope `{scope}`{needs}): "
                    f"{g['issue']}",
                f"  Opinion lines {lines[0]}-{lines[1]}:",
                f"{FENCE}\n{(g.get('opinion_comment') or '').strip()}\n{FENCE}"]

    items = [(it, jid) for it, jid in job_ids(r) if jid]
    if not items:
        out += ["", "**The agent named no issue on this provision.** Every defect "
                    "above is a miss the reviewer has to confirm."]
        return out, 0
    out += ["", f"The agent named **{len(items)}** issue(s) here — judged items:"]
    for it, jid in items:
        counter[0] += 1
        a = align.get(jid)
        item = f"j{counter[0]:02d}"
        out += ["", f"##### {item}  (agent: risk type {it['type']}, probability "
                    f"{it['prob']})",
                f"Agent's issue: {it['issue']}",
                f"Judge's call: **{judge_call(a)}**"]
        if a:
            for label, key in (("What the judge says the words at issue are",
                                "words_at_issue"),
                               ("Judge's quote from the passage", "evidence"),
                               ("Judge's reason", "reason")):
                if (a.get(key) or "").strip():
                    out.append(f"{label}: {a[key].strip()}")
    return out, len(items)


def other_block(r, src):
    """One provision no court construed, with the agent's issues on it."""
    issues = [it for it in json.loads(r["issues"]) if it.get("issue")]
    out = [f"- **{r['clause_id']} — {src['clause_name']}**: "
           f"\"{clip(lib.normalise(src['clause_text']), LOCATOR)}\""]
    out += [f"  - (type {it['type']}, p {it['prob']}) {it['issue']}" for it in issues]
    return out


def packet(citation, rows, ds, align):
    """The review packet for one case, and how many judged items it holds."""
    counter = [0]
    out = []
    by_contract = defaultdict(list)
    for r in rows:
        by_contract[r["contract_id"]].append(r)
    for cid in sorted(by_contract):
        prov = sorted(by_contract[cid], key=lambda r: r["clause_id"])
        construed = [r for r in prov if r["gold"] == "risky"]
        others = [r for r in prov if r["gold"] != "risky" and r["issues"] != "[]"]
        out += [f"### Contract {cid}", "",
                f"{len(prov)} provisions judged by the agent; the court construed "
                f"{len(construed)}; the agent named issues on {len(others)} of the "
                f"others."]
        for r in construed:
            block, _ = construed_block(r, ds[(cid, r["clause_id"])], align, counter)
            out += [""] + block
        if others:
            out += ["", f"#### The agent's issues on provisions no court construed "
                        f"({len(others)})", ""]
            for r in others:
                out += other_block(r, ds[(cid, r["clause_id"])])
        out.append("")
    head = (f"**{counter[0]} judged item(s)** for the reviewer to decide in this case"
            + ("" if counter[0] else " — the court construed nothing the agent could "
               "be checked against, so the reviewer only looks over the agent's flags")
            + ".\n")
    return head + "\n" + "\n".join(out), counter[0]


def check(answer):
    """Warnings about an answer that is well-formed but inconsistent."""
    why = []
    s, d = answer.get("score"), answer.get("difficulty")
    if not isinstance(s, int) or not 1 <= s <= 10:
        why.append(f"score {s!r} is not an integer 1-10")
    elif d in DIFFICULTY and s not in DIFFICULTY[d]:
        why.append(f"score {s} does not agree with difficulty {d!r}")
    return why


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", action="append", metavar="CITATION",
                    help="one citation; repeat for several")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--effort", default=EFFORT)
    ap.add_argument("--out", help="directory for a trial run's result and logs, "
                                  "instead of output/")
    ap.add_argument("--dry-run", action="store_true",
                    help="build every packet and print its size; no calls")
    args = ap.parse_args()

    out_dir = Path(args.out) if args.out else None
    stage = out_dir or lib.DIFFICULTY_OUT
    result = stage / "review_difficulty.json"
    log_dir = stage / "llm_logs" / PROMPT

    cases = lib.read_json(lib.DATASET_OUT / "cases.json", {})
    registry = lib.read_json(lib.DATASET_OUT / "contracts.json", {})
    ds = {(r["contract_id"], r["clause_id"]): r
          for r in csv.DictReader(open(DATASET, encoding="utf-8"))}
    align = lib.read_json(ALIGNMENT, {})
    by_case = defaultdict(list)
    for r in csv.DictReader(open(PREDS, encoding="utf-8")):
        if r["ok"] == "1":
            by_case[r["citation"]].append(r)
    done = lib.read_json(result, {}) or {}

    if args.case:
        unknown = [c for c in args.case if c not in by_case]
        if unknown:
            raise SystemExit(f"not in the evaluation set: {unknown}")
    todo = [c for c in sorted(by_case)
            if (not args.case or c in args.case) and (args.dry_run or c not in done)]

    def material(citation):
        rows = by_case[citation]
        ids = sorted({r["contract_id"] for r in rows})
        texts = {cid: (lib.ROOT / registry[cid]["file"]).read_text(encoding="utf-8")
                 for cid in ids}
        opinion = lib.numbered((lib.OPINIONS / f"{cases[citation]['id']}.txt")
                               .read_text(encoding="utf-8"))
        pk, n_items = packet(citation, rows, ds, align)
        return opinion, documents(ids, texts), pk, n_items

    if args.dry_run:
        total = 0
        for c in todo:
            opinion, contracts, pk, n = material(c)
            t = lib.n_tokens(opinion + contracts + pk)
            total += t
            print(f"{c:24s} {n:3d} judged item(s)  {t:>8,} tokens")
        print(f"\n{len(todo)} case(s), {total:,} input tokens before the prompt's own text")
        return

    def one(citation):
        opinion, contracts, pk, n_items = material(citation)
        why = lib.out_of_bounds(opinion + contracts + pk)
        if why:
            print(f"skip   {citation}: {why}", flush=True)
            return citation, None
        answer = lib.ask(PROMPT, cases[citation]["id"], effort=args.effort,
                         model=args.model, log_dir=log_dir, citation=citation,
                         opinion=opinion, contracts=contracts, packet=pk)
        if answer is None:
            return citation, None
        answer["judged_items"] = n_items
        answer["warnings"] = check(answer)
        return citation, answer

    if todo:
        with cf.ThreadPoolExecutor(max_workers=args.parallel) as pool:
            for fut in cf.as_completed([pool.submit(one, c) for c in todo]):
                citation, answer = fut.result()
                if answer is None:
                    continue
                done[citation] = answer
                lib.write_json(result, done, quiet=True)
                print(f"{answer['difficulty']:12s} {answer['score']:2d}  "
                      f"~{answer['estimated_minutes']} min  {citation}  "
                      f"({answer['judged_items']} item(s))", flush=True)
                for w in answer["warnings"]:
                    print(f"  ! {w}", flush=True)

    spread = Counter(v["difficulty"] for v in done.values())
    print(f"\n{len(done)} case(s): " + ", ".join(
        f"{d} {spread[d]}" for d in DIFFICULTY if spread[d]))


if __name__ == "__main__":
    main()

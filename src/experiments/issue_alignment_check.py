"""Issue-alignment check — was the agent right for the right reason?

Risk detection measures ranking: did the model put the litigated provisions above
the rest. It cannot tell whether it did so for the reason the court actually
had. A model can flag the right provision while naming a defect the court never
touched, and every metric in risk detection scores that as a hit.

So: for each issue the agent named, ask a DIFFERENT model — `gpt-5.6-sol`, which
never saw the agent's reasoning and is not the family being judged — whether the
defect it named is one the court construed, judged only against the court's own
verbatim words.

**Scope.** Only issues whose provision AND risk type both match the gold label.
An issue on a provision no court construed has no passage to check it against,
and an issue of the wrong type is already counted wrong by risk detection's
one-vs-rest panels. This is a precision-of-explanation measure, conditional on
the provision and type being right — not a second shot at the ranking.

A court commonly construes several defects of one provision in one passage, so
an issue counts as aligned when it matches ANY of them; it does not have to be
the court's main point. That is why the judge returns a score rather than a
verdict, and why `--threshold` is a reporting choice rather than something the
judge is told.

**Control.** `--control` pairs every issue with a DIFFERENT case's opinion
passage. The judge should reject those. If it does not, it is not discriminating
and the real numbers mean nothing, so run it before believing them:

    python src/experiments/issue_alignment_check.py --control --out /tmp/alignment-control

Usage:
    python src/experiments/issue_alignment_check.py                 # the real run
    python src/experiments/issue_alignment_check.py --parallel 8
    python src/experiments/issue_alignment_check.py --report        # score what exists
"""
import argparse
import concurrent.futures as cf
import csv
import json
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

MODEL = lib.MODEL          # gpt-5.6-sol — NOT the family being judged
EFFORT = "high"
PREDS = lib.OUT / "risk_detect_agent_preds.csv"
DATASET = lib.OUT / "dataset.csv"
OUT = lib.OUT / "issue_alignment_check.json"
CSV_OUT = lib.OUT / "issue_alignment_check.csv"
SHARDS = "issue_alignment_check"

FIELDS = ["job_id", "contract_id", "clause_id", "citation", "clause_name",
          "taxonomy", "taxonomy_provenance", "type", "prob", "issue",
          "alignment", "determinable", "court_defect", "evidence", "reason"]


def whole_opinion(js):
    """Swap each job's captured passage for the entire opinion.

    Step 1 records ONE contiguous line range per clause — a median 4.2% of the
    opinion — so a defect the court took up somewhere else in the same opinion
    is invisible to the check. Re-judging against the whole opinion says how
    much that costs: if a misaligned issue stays misaligned when the judge can
    see everything the court wrote, the single range was not the limit.
    """
    cases = lib.read_json(lib.OUT / "cases.json", {}) or {}
    ids = {cit: c["id"] for cit, c in cases.items()}
    out = []
    for j in js:
        p = lib.OPINIONS / f"{ids.get(j['citation'], '?')}.txt"
        if not p.exists():
            continue
        k = dict(j)
        k["opinion_comment"] = p.read_text(encoding="utf-8")
        out.append(k)
    return out


def jobs(preds, dataset):
    """One job per named issue whose provision and risk type both match gold.

    Keyed on (contract_id, clause_id, type, ordinal) so a provision carrying two
    issues of one type yields two jobs that cannot collide in the shard store.
    """
    ds = {(r["contract_id"], r["clause_id"]): r for r in dataset}
    out = []
    for r in preds:
        if r["ok"] != "1" or r["gold"] == "not_risky":
            continue
        gold = {1: int(r["gold_type1"]), 2: int(r["gold_type2"])}
        src = ds.get((r["contract_id"], r["clause_id"]))
        if src is None or not src["opinion_comment"].strip():
            continue
        seen = {1: 0, 2: 0}
        for it in json.loads(r["issues"]):
            t = it.get("type")
            if not it.get("issue") or t not in (1, 2) or not gold[t]:
                continue
            seen[t] += 1
            out.append({
                "job_id": f"{r['contract_id']}__{r['clause_id']}__t{t}_{seen[t]}",
                "contract_id": r["contract_id"], "clause_id": r["clause_id"],
                "citation": src["citation"], "clause_name": src["clause_name"],
                "clause_text": src["clause_text"],
                "opinion_comment": src["opinion_comment"],
                "taxonomy": r["taxonomy"],
                "taxonomy_provenance": r.get("taxonomy_provenance", ""),
                "type": t, "prob": it["prob"], "issue": it["issue"],
            })
    return sorted(out, key=lambda j: j["job_id"])


def scramble(js, mode="corpus", seed=0):
    """Re-pair every job with an opinion passage that is not its own.

    Two nulls, because they test different things:

    `corpus` — a passage from a DIFFERENT case. Nothing lines up: parties,
    subject matter and vocabulary all differ, so a judge that merely pattern-
    matches "legal text about a contract" is caught. This is the easy null.

    `case` — a passage from the SAME case but a different provision. The
    parties, the instrument and the vocabulary are shared, and only the defect
    differs, so passing it means the judge is discriminating between defects
    rather than between documents. This is the null that matters, and the one
    where a false positive is not automatically an error: a court that construes
    two provisions together can genuinely reach the same defect in both.
    """
    rnd = random.Random(seed)
    out = []
    for j in js:
        if mode == "case":
            others = [o for o in js if o["citation"] == j["citation"]
                      and o["opinion_comment"] != j["opinion_comment"]]
        else:
            others = [o for o in js if o["citation"] != j["citation"]]
        if not others:
            continue
        foil = rnd.choice(others)
        k = dict(j)
        k["opinion_comment"] = foil["opinion_comment"]
        k["citation"] = foil["citation"]          # the prompt names the source
        k["foil_from"] = f"{foil['contract_id']}/{foil['clause_id']}"
        out.append(k)
    return out


def type_def(t):
    """The taxonomy lines for one risk type, as the prompt's `type_def`."""
    return "\n".join(f"  [{c}] {txt}" for c, txt in sorted(lib.RISK_TYPES.items())
                     if c.startswith(str(t)))


def judge(job, log_as):
    a = lib.ask("issue_alignment_check", job["job_id"], effort=EFFORT, model=MODEL, log_as=log_as,
                citation=job["citation"], contract_id=job["contract_id"],
                clause_name=job["clause_name"], clause_text=job["clause_text"],
                type_def=type_def(job["type"]), issue_text=job["issue"],
                opinion_comment=job["opinion_comment"].strip())
    if a is None:
        return None
    if not isinstance(a.get("alignment"), (int, float)):
        return None
    # An undecidable passage scores 0 whatever the model put in the field: the
    # prompt says so, and letting the two disagree would make the column mean
    # two things.
    if not a.get("determinable"):
        a["alignment"] = 0.0
    a["alignment"] = min(max(float(a["alignment"]), 0.0), 1.0)
    return a


def run(js, done, keep, log_as, parallel):
    """Judge every outstanding job. `keep(job_id, record)` stores one result.

    The real run shards to disk so it can resume; the control keeps its results
    in memory, so that a throwaway experiment leaves nothing in `output/`.
    """
    todo = [j for j in js if j["job_id"] not in done]
    print(f"{len(done)} judged, {len(todo)} to run "
          f"({MODEL}, effort {EFFORT}, {parallel} at a time)")

    def one(i, j):
        a = judge(j, log_as)
        if a is None:
            print(f"  [{i}/{len(todo)}] {j['job_id']}: no answer", flush=True)
            return
        keep(j["job_id"], {**j, **a})
        print(f"  [{i}/{len(todo)}] {j['job_id']}: "
              f"alignment {a['alignment']:.2f}"
              f"{'' if a.get('determinable') else '  (not determinable)'}",
              flush=True)

    with cf.ThreadPoolExecutor(max_workers=parallel) as pool:
        futs = [pool.submit(one, i, j) for i, j in enumerate(todo, 1)]
        for f in cf.as_completed(futs):
            f.result()


def report(res, threshold, label=""):
    """Alignment rate overall, by risk type and by taxonomy provenance."""
    rows = sorted(res.values(), key=lambda r: r["job_id"])
    if not rows:
        print("nothing to report")
        return rows
    det = [r for r in rows if r.get("determinable")]

    def rate(rs):
        if not rs:
            return "        n=0"
        hit = sum(1 for r in rs if r["alignment"] >= threshold)
        med = statistics.median([r["alignment"] for r in rs])
        return f"{hit:4d}/{len(rs):<4d} = {hit/len(rs):6.1%}   median {med:.2f}"

    print(f"\n=== alignment{label} (threshold {threshold}) ===\n")
    print(f"  judged                    {len(rows)}")
    print(f"  determinable              {len(det)} ({len(det)/len(rows):.1%})")
    print(f"\n  ALL                       {rate(rows)}")
    print(f"  determinable only         {rate(det)}")
    print(f"\n  by risk type")
    for t in (1, 2):
        print(f"    type {t}                  {rate([r for r in rows if r['type'] == t])}")
    print(f"\n  by taxonomy provenance")
    for p in ("westlaw", "model"):
        print(f"    {p:<22}{rate([r for r in rows if r['taxonomy_provenance'] == p])}")
    print(f"\n  by gold taxonomy")
    for tx in sorted({r["taxonomy"] for r in rows}):
        print(f"    {tx:<22}{rate([r for r in rows if r['taxonomy'] == tx])}")

    a = sorted(r["alignment"] for r in rows)
    q = lambda p: a[min(len(a) - 1, int(p * len(a)))]  # noqa: E731
    print(f"\n  score distribution        p10 {q(.10):.2f}  p25 {q(.25):.2f}  "
          f"median {q(.50):.2f}  p75 {q(.75):.2f}  p90 {q(.90):.2f}")
    return rows


def write_csv(rows, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"\n-> {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="run only N jobs")
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--control", choices=["corpus", "case"], nargs="?",
                    const="corpus", default=None,
                    help="score against a passage that is not this issue's: "
                         "'corpus' from another case, 'case' from another "
                         "provision of the same case (the harder null)")
    ap.add_argument("--out", help="directory for the control run's artifacts")
    ap.add_argument("--report", action="store_true", help="score what exists, no calls")
    ap.add_argument("--full-opinion", action="store_true",
                    help="judge against the WHOLE opinion instead of the one "
                         "passage step 1 recorded (diagnostic; needs --out)")
    ap.add_argument("--rejudge-below", type=float, metavar="X",
                    help="only issues already scored below X in the real run")
    args = ap.parse_args()

    preds = list(csv.DictReader(open(PREDS, newline="", encoding="utf-8")))
    dataset = list(csv.DictReader(open(DATASET, newline="", encoding="utf-8")))
    js = jobs(preds, dataset)
    print(f"{len(js)} issue(s) with provision and risk type both matching gold, "
          f"over {len({j['clause_id'] + j['contract_id'] for j in js})} provision(s)")

    if args.rejudge_below is not None:
        prior = {r["job_id"]: float(r["alignment"]) for r in
                 csv.DictReader(open(CSV_OUT, newline="", encoding="utf-8"))}
        js = [j for j in js if prior.get(j["job_id"], 1.0) < args.rejudge_below]
        print(f"  restricted to {len(js)} issue(s) scored below "
              f"{args.rejudge_below} in the real run")

    if args.control:
        js = scramble(js, args.control)
    if args.full_opinion:
        js = whole_opinion(js)
        print(f"  judging against the WHOLE opinion, {len(js)} job(s)")

    diagnostic = args.control or args.full_opinion
    if diagnostic:
        if not args.out:
            sys.exit("--control and --full-opinion need --out <dir> — a "
                     "diagnostic should leave nothing in output/")
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        tag = f"control_{args.control}" if args.control else "rejudged"
        tag += "_full" if args.full_opinion else ""
        json_out, csv_out = out_dir / f"{tag}.json", out_dir / f"{tag}.csv"
        bits = []
        if args.control:
            bits.append("passage from " + ("another case" if args.control ==
                        "corpus" else "another provision of the SAME case"))
        if args.full_opinion:
            bits.append("the WHOLE opinion, not the recorded passage")
        label, log_as = f" — DIAGNOSTIC: {'; '.join(bits)}", f"alignment_{tag}"
    else:
        json_out, csv_out = OUT, CSV_OUT
        label, log_as = "", "issue_alignment_check"

    if args.limit:
        js = js[:args.limit]

    if args.report:
        res = {**(lib.read_json(json_out, {}) or {}), **lib.read_shards(SHARDS)}
    elif diagnostic:
        res = {}
        run(js, res, res.__setitem__, log_as, args.parallel)
    else:
        res = lib.read_shards(SHARDS)
        run(js, res, lambda k, v: lib.write_shard(SHARDS, k, k, v),
            log_as, args.parallel)
        res = lib.merge_shards(SHARDS, OUT)

    rows = report(res, args.threshold, label)
    if rows and not args.report:
        if diagnostic:
            # Not lib.write_json: a diagnostic writes outside the repo on
            # purpose, and that helper reports paths relative to ROOT.
            json_out.write_text(json.dumps(res, ensure_ascii=False, indent=2,
                                           sort_keys=True), encoding="utf-8")
            print(f"\n-> {json_out}")
        write_csv(rows, csv_out)


if __name__ == "__main__":
    main()

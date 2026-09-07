"""Issue-alignment check — was the agent right for the right reason, and which?

Risk detection measures ranking: did the model put the litigated provisions above
the rest. It cannot tell whether it did so for the reason the court actually
had. A model can flag the right provision while naming a defect the court never
touched, and every metric in risk detection scores that as a hit.

So: for each issue the agent named, ask a DIFFERENT model — `gpt-5.6-sol`, which
never saw the agent's reasoning and is not the family being judged — whether the
defect it named is one the court construed, judged against the court's own
verbatim words.

**What is new here is `matched`.** Step 2 records every distinct defect a court
construed in a provision, each with its own passage, so the judge does not
merely say "aligned" — it says WHICH recorded defect the agent found:

    precision  of the issues the agent named, how many name a defect the
               court actually construed
    recall     of the defects the court construed, how many the agent found

Recall was not computable before: one passage per provision collapsed several
defects into one target, so what was reported as recall was really target
coverage, an upper bound.

**Scope.** Only issues whose provision AND risk type both match the gold label.
An issue on a provision no court construed has no passage to check it against,
and an issue of the wrong type is already counted wrong by risk detection's
one-vs-rest panels. Precision here is therefore conditional on the provision and
type being right; recall is not — it is over every gold issue in the corpus,
including those on provisions the agent said nothing about.

**Control.** `--control` pairs every issue with defects that are not its own.
The judge should reject those. If it does not, it is not discriminating and the
real numbers mean nothing, so run it before believing them:

    python src/experiments/issue_alignment_check.py --control --out /tmp/align

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
          "matched", "matched_key", "gold_issue", "alignment", "determinable",
          "court_defect", "evidence", "reason"]


def gold_issues(dataset):
    """Every gold issue in the corpus, keyed so a match can be counted once.

    The key is positional within its provision — `<contract>__<clause>__i2` —
    and step 2 stores the list in a fixed order, so the same gold issue keeps
    the same key across runs. This is the recall denominator.
    """
    out = {}
    for r in dataset:
        if r["label"] != "POSITIVE":
            continue
        for n, g in enumerate(json.loads(r["issues"]), 1):
            key = f"{r['contract_id']}__{r['clause_id']}__i{n}"
            out[key] = {**g, "key": key, "citation": r["citation"],
                        "clause_name": r["clause_name"],
                        "contract_id": r["contract_id"],
                        "clause_id": r["clause_id"]}
    return out


def render(cands):
    """The candidate block the prompt shows, ids and all."""
    return "\n\n".join(
        f"#### {c['id']}\n\n"
        f"Recorded defect: {c['issue']}\n\n"
        f"Verbatim from the opinion, lines "
        f"{c['opinion_lines'][0]}-{c['opinion_lines'][1]}:\n\n"
        f"```\n{c['opinion_comment'].strip()}\n```"
        for c in cands)


def jobs(preds, dataset):
    """One job per named issue whose provision and risk type both match gold.

    The candidates are the gold issues of that provision whose fine code falls
    under the coarse type the agent named — `1.1` and `1.3` both answer type 1.
    An issue with no candidate is out of scope: either the provision was never
    construed, or it was construed only under the other type.

    Keyed on (contract_id, clause_id, type, ordinal) so a provision carrying two
    issues of one type yields two jobs that cannot collide in the shard store.
    """
    gold = gold_issues(dataset)
    by_clause = {}
    for g in gold.values():
        by_clause.setdefault((g["contract_id"], g["clause_id"]), []).append(g)
    ds = {(r["contract_id"], r["clause_id"]): r for r in dataset}

    out = []
    for r in preds:
        if r["ok"] != "1" or r["gold"] == "not_risky":
            continue
        src = ds.get((r["contract_id"], r["clause_id"]))
        if src is None:
            continue
        here = by_clause.get((r["contract_id"], r["clause_id"]), [])
        seen = {1: 0, 2: 0}
        for it in json.loads(r["issues"]):
            t = it.get("type")
            if not it.get("issue") or t not in (1, 2):
                continue
            cands = [{**g, "id": f"g{n}"} for n, g in
                     enumerate((x for x in here
                                if x["risk_type"].startswith(str(t))), 1)]
            if not cands:
                continue
            seen[t] += 1
            out.append({
                "job_id": f"{r['contract_id']}__{r['clause_id']}__t{t}_{seen[t]}",
                "contract_id": r["contract_id"], "clause_id": r["clause_id"],
                "citation": src["citation"], "clause_name": src["clause_name"],
                "clause_text": src["clause_text"],
                "taxonomy": r["taxonomy"],
                "taxonomy_provenance": r.get("taxonomy_provenance", ""),
                "type": t, "prob": it["prob"], "issue": it["issue"],
                "candidates": cands,
            })
    return sorted(out, key=lambda j: j["job_id"])


def scramble(js, mode="corpus", seed=0):
    """Re-pair every job with candidates that are not its own.

    Two nulls, because they test different things:

    `corpus` — candidates from a DIFFERENT case. Nothing lines up: parties,
    subject matter and vocabulary all differ, so a judge that merely pattern-
    matches "legal text about a contract" is caught. This is the easy null.

    `case` — candidates from the SAME case but a different provision. The
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
                      and o["clause_id"] != j["clause_id"]]
        else:
            others = [o for o in js if o["citation"] != j["citation"]]
        if not others:
            continue
        foil = rnd.choice(others)
        k = dict(j)
        k["candidates"] = foil["candidates"]
        k["citation"] = foil["citation"]          # the prompt names the source
        k["foil_from"] = f"{foil['contract_id']}/{foil['clause_id']}"
        out.append(k)
    return out


def type_def(t):
    """The taxonomy lines for one risk type, as the prompt's `type_def`."""
    return "\n".join(f"  [{c}] {txt}" for c, txt in sorted(lib.RISK_TYPES.items())
                     if c.startswith(str(t)))


def judge(job, log_as):
    a = lib.ask("issue_alignment_check", job["job_id"], effort=EFFORT,
                model=MODEL, log_as=log_as,
                citation=job["citation"], contract_id=job["contract_id"],
                clause_name=job["clause_name"], clause_text=job["clause_text"],
                type_def=type_def(job["type"]), issue_text=job["issue"],
                candidates=render(job["candidates"]))
    if a is None:
        return None
    if not isinstance(a.get("alignment"), (int, float)):
        return None

    # `matched` must name a candidate that was actually shown. An id outside the
    # list is not a near miss to be salvaged — it means the answer is not about
    # the material, so it is treated as no match rather than trusted.
    by_id = {c["id"]: c for c in job["candidates"]}
    hit = by_id.get(str(a.get("matched") or "").strip())
    a["matched"] = hit["id"] if hit else ""
    a["matched_key"] = hit["key"] if hit else ""
    a["gold_issue"] = hit["issue"] if hit else ""

    # Three ways to score zero, kept consistent so the column means one thing:
    # an undecidable passage, no candidate chosen, and a score the model itself
    # put at zero. The prompt states the first two; enforcing them here stops
    # the score and the verdict disagreeing.
    if not a.get("determinable") or not hit:
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
              f"{a['matched'] or '--':<3} alignment {a['alignment']:.2f}"
              f"{'' if a.get('determinable') else '  (not determinable)'}",
              flush=True)

    with cf.ThreadPoolExecutor(max_workers=parallel) as pool:
        futs = [pool.submit(one, i, j) for i, j in enumerate(todo, 1)]
        for f in cf.as_completed(futs):
            f.result()


def report(res, threshold, n_gold, n_reachable, label=""):
    """Precision over named issues, recall over the court's own defects."""
    rows = sorted(res.values(), key=lambda r: r["job_id"])
    if not rows:
        print("nothing to report")
        return rows
    det = [r for r in rows if r.get("determinable")]
    ok = [r for r in rows if r["alignment"] >= threshold and r["matched_key"]]
    found = {r["matched_key"] for r in ok}

    def rate(rs):
        if not rs:
            return "        n=0"
        hit = sum(1 for r in rs if r["alignment"] >= threshold)
        med = statistics.median([r["alignment"] for r in rs])
        return f"{hit:4d}/{len(rs):<4d} = {hit/len(rs):6.1%}   median {med:.2f}"

    print(f"\n=== alignment{label} (threshold {threshold}) ===\n")
    print(f"  judged                    {len(rows)}")
    print(f"  determinable              {len(det)} ({len(det)/len(rows):.1%})")
    print(f"\n  PRECISION  {rate(rows)}")
    print(f"    determinable only       {rate(det)}")

    # Recall is reported twice because the two answer different questions. Over
    # every gold issue it is the honest headline: how much of what the courts
    # construed did the agent find. Over the reachable ones it isolates the
    # explanation from the ranking — a defect on a provision the agent never
    # flagged, or flagged only under the other type, was never in reach of this
    # measure, and that is a risk-detection miss, not a wrong reason.
    if n_gold:
        print(f"\n  RECALL     {len(found):4d}/{n_gold:<4d} = "
              f"{len(found)/n_gold:6.1%}   of every gold issue in the corpus")
    if n_reachable:
        print(f"    reachable only          {len(found):4d}/{n_reachable:<4d} = "
              f"{len(found)/n_reachable:6.1%}   (gold issues this check could "
              f"reach at all)")

    dup = len(ok) - len(found)
    if dup > 0:
        print(f"\n  {dup} aligned issue(s) matched a gold issue another issue "
              f"had already matched — counted once in recall")

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
                    help="judge against defects that are not this issue's: "
                         "'corpus' from another case, 'case' from another "
                         "provision of the same case (the harder null)")
    ap.add_argument("--out", help="directory for the control run's artifacts")
    ap.add_argument("--report", action="store_true", help="score what exists, no calls")
    ap.add_argument("--rejudge-below", type=float, metavar="X",
                    help="only issues already scored below X in the real run")
    args = ap.parse_args()

    if not PREDS.exists():
        sys.exit(f"{PREDS} does not exist — run the risk-detection experiment "
                 f"first, then this")

    preds = list(csv.DictReader(open(PREDS, newline="", encoding="utf-8")))
    dataset = list(csv.DictReader(open(DATASET, newline="", encoding="utf-8")))
    gold = gold_issues(dataset)
    js = jobs(preds, dataset)
    reachable = {c["key"] for j in js for c in j["candidates"]}
    print(f"{len(js)} issue(s) with provision and risk type both matching gold, "
          f"over {len({(j['contract_id'], j['clause_id']) for j in js})} provision(s)")
    print(f"{len(gold)} gold issues in the corpus, {len(reachable)} reachable "
          f"by this check")

    if args.rejudge_below is not None:
        prior = {r["job_id"]: float(r["alignment"]) for r in
                 csv.DictReader(open(CSV_OUT, newline="", encoding="utf-8"))}
        js = [j for j in js if prior.get(j["job_id"], 1.0) < args.rejudge_below]
        print(f"  restricted to {len(js)} issue(s) scored below "
              f"{args.rejudge_below} in the real run")

    if args.control:
        js = scramble(js, args.control)

    if args.control:
        if not args.out:
            sys.exit("--control needs --out <dir> — a diagnostic should leave "
                     "nothing in output/")
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        tag = f"control_{args.control}"
        json_out, csv_out = out_dir / f"{tag}.json", out_dir / f"{tag}.csv"
        where = ("another case" if args.control == "corpus"
                 else "another provision of the SAME case")
        label = f" — DIAGNOSTIC: candidates from {where}"
        log_as = f"alignment_{tag}"
    else:
        json_out, csv_out = OUT, CSV_OUT
        label, log_as = "", "issue_alignment_check"

    if args.limit:
        js = js[:args.limit]

    if args.report:
        res = {**(lib.read_json(json_out, {}) or {}), **lib.read_shards(SHARDS)}
    elif args.control:
        res = {}
        run(js, res, res.__setitem__, log_as, args.parallel)
    else:
        res = lib.read_shards(SHARDS)
        run(js, res, lambda k, v: lib.write_shard(SHARDS, k, k, v),
            log_as, args.parallel)
        res = lib.merge_shards(SHARDS, OUT)

    # A control's recall is meaningless against the real corpus — its jobs were
    # deliberately paired with the wrong defects — so only precision is scored.
    rows = report(res, args.threshold,
                  0 if args.control else len(gold),
                  0 if args.control else len(reachable), label)
    if rows and not args.report:
        if args.control:
            # Not lib.write_json: a diagnostic writes outside the repo on
            # purpose, and that helper reports paths relative to ROOT.
            json_out.write_text(json.dumps(res, ensure_ascii=False, indent=2,
                                           sort_keys=True), encoding="utf-8")
            print(f"\n-> {json_out}")
        write_csv(rows, csv_out)


if __name__ == "__main__":
    main()

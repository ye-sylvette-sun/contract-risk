"""Experiment 3 — few-shot with judicial reasoning, one LLM API call per contract.

The question: given a contract and its provisions, can a model pick out the ones
a federal court actually construed?

Everything goes into a single prompt — no tools, no agent loop, no second turn.
**One call per contract, judging every provision at once**, which is a
methodological requirement: Category 2 risk is about how provisions sit against
each other, so a model shown half a contract is being asked an easier question.
The call carries the FULL contract text, every provision to judge quoted under
an opaque id, and one worked PAIR per risk category — a clause a court construed,
the court's own words about it, and a clause from the same contract that no
court construed, framed as LOWER RISK and never as clean.

The judicial excerpt is the point of this experiment: `opinion_comment` is
already in the dataset, so the model is shown what the two sides argued rather
than left to guess what "risky" means from a bare label.

Per provision it returns two INDEPENDENT probabilities — `prob_cat1` intrinsic,
`prob_cat2` relational — with separate reasoning. A category is flagged at 0.5, but
the raw probabilities are what get written out, so the operating threshold is
chosen afterwards.

Gold: POSITIVE with taxonomy 1.x -> risky_cat1; 2.x -> risky_cat2; NEGATIVE ->
not_risky.

The example contracts are EXCLUDED from evaluation, so no clause the model was
taught on is also scored.

Input : output/dataset.csv, output/contracts/<cid>.md
Output: output/exp3_llm_api_preds.csv      per-provision predictions (resumable)
        output/exp3_llm_api/<cid>.json     the model's judgments, gold injected
        output/llm_logs/exp3_llm_api/<cid>.json  full prompt, response and usage

Usage:
    python src/experiments/exp3_llm_api.py [--limit N] [--parallel N] [--dry-run]
    python src/experiments/exp3_llm_api.py --metrics-only
"""
import argparse
import concurrent.futures as cf
import csv
import json
import os
import random
import sys
from collections import OrderedDict, defaultdict

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import lib  # noqa: E402

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

PREDS = lib.OUT / "exp3_llm_api_preds.csv"
RAW = lib.OUT / "exp3_llm_api"

# Nothing is batched. Both budgets have room for the largest contract in the
# corpus — 418 provisions, 481,007 characters:
#
#   input   203,311 tokens against lib.MAX_INPUT_TOKENS = 900,000
#   output  ~46,000 tokens of JSON against lib.MAX_OUTPUT = 128,000,
#           leaving ~82,000 for reasoning
#
# The output ceiling is the model's own (GET /v1/models says max_tokens 128000),
# so there is no headroom beyond it. If a contract ever did run past it the call
# would be lost rather than usefully truncated, and the answer would be to split
# THAT contract, knowingly, not to batch every contract by default.

COARSE = ["risky_cat1", "risky_cat2", "not_risky"]
FLAG = 0.5

# `gold_subtype` is ground truth and free to carry; there is no predicted
# subtype — only the two categories are asked for.
FIELDS = ["contract_id", "citation", "clause_id", "clause_name",
          "label", "taxonomy", "gold", "gold_subtype", "pred",
          "prob_cat1", "prob_cat2", "reasoning_cat1", "reasoning_cat2", "ok"]

CATEGORY_NAME = {
    "1.1": "Category 1.1 — lexical ambiguity or vagueness",
    "1.2": "Category 1.2 — mechanical error",
    "1.3": "Category 1.3 — general-vs-specific / list scope",
    "2.1": "Category 2.1 — conflicting clauses",
    "2.2": "Category 2.2 — whole-instrument incoherence",
    "2.3": "Category 2.3 — recitals vs operative text",
}


# ------------------------------------------------------------------ gold ----
def gold_of(row):
    if row["label"] != "POSITIVE":
        return "not_risky"
    return "risky_cat1" if row["taxonomy"].startswith("1") else "risky_cat2"


def gold_fine(row):
    return row["taxonomy"] if row["label"] == "POSITIVE" else "none"


def _f(x, default=0.0):
    try:
        return min(max(float(x), 0.0), 1.0)
    except (TypeError, ValueError):
        return default


def pred_from_probs(p1, p2):
    """Coarse label at FLAG. Both below -> not_risky; else the likelier category."""
    if p1 < FLAG and p2 < FLAG:
        return "not_risky"
    return "risky_cat1" if p1 >= p2 else "risky_cat2"


def flat(text):
    return " ".join(str(text).split())


# -------------------------------------------------------------- examples ----
EXCERPT_CAP = 6_000     # characters of opinion an example may carry


def pick_examples(rows):
    """One worked pair per risk category present in the data.

    Deterministic, never random: per taxonomy code, the positive with the
    longest `opinion_comment` that still fits under EXCERPT_CAP (the longest in
    the corpus runs to 38,000 characters and would swamp the others). Where
    every candidate is over the cap the shortest is used whole — reasoning cut
    off mid-sentence is worse than a different example. Ties break on clause id.

    Each is paired with a clause from the SAME contract that no court construed,
    so the contrast is within a document. The pair is a scale, not a right
    answer.
    """
    by_contract = defaultdict(list)
    for r in rows:
        by_contract[r["contract_id"]].append(r)

    examples = []
    for code in sorted({r["taxonomy"] for r in rows if r["label"] == "POSITIVE"}):
        cands = [r for r in rows
                 if r["label"] == "POSITIVE" and r["taxonomy"] == code
                 and r["opinion_comment"].strip()]
        if not cands:
            continue
        fits = [r for r in cands if len(r["opinion_comment"]) <= EXCERPT_CAP]
        best = (max(fits, key=lambda r: (len(r["opinion_comment"]), r["clause_id"]))
                if fits else
                min(cands, key=lambda r: (len(r["opinion_comment"]), r["clause_id"])))
        pool = by_contract[best["contract_id"]]
        foils = [r for r in pool if r["label"] == "NEGATIVE"]
        foil = max(foils, key=lambda r: (len(r["clause_text"]), r["clause_id"])) \
            if foils else None
        examples.append({"code": code, "row": best, "foil": foil,
                         "n_pos": len(pool) - len(foils), "n_neg": len(foils)})
    return examples


def example_block(examples):
    """The worked examples, paired high-risk against lower-risk.

    The second half of each pair must never be presented as clean: all that is
    known is that no court construed it. Calling it "not risky" would teach a
    fact the data does not support, and turn the pair into an instruction to
    match good drafting rather than a scale.
    """
    out = ["One worked pair per risk type. Each pair is two real provisions from "
           "the same contract: one a federal court construed — so it is KNOWN to "
           "be high risk, and the court's own words about it follow — and one no "
           "court construed in that case, which makes it LOWER RISK on the "
           "evidence available and nothing stronger than that.\n"
           "\nEach example is introduced with the full count for its contract: how "
           "many of its provisions a court construed, and how many it did not. "
           "That ratio is what a real contract looks like — the construed "
           "provisions are always a small minority. Read it as the base rate to "
           "expect, NOT as a quota to reproduce: the contract you are judging may "
           "have more such provisions, or none at all.\n"]
    for e in examples:
        r, foil = e["row"], e["foil"]
        out.append(f"\n================ {CATEGORY_NAME[e['code']]} ================")
        out.append(f"From {r['citation']}, contract {r['contract_id']}.")
        out.append(f"In this contract a court construed {e['n_pos']} provision"
                   f"{'' if e['n_pos'] == 1 else 's'}; the other {e['n_neg']} it "
                   f"did not.\n")
        out.append(f'HIGH RISK — "{r["clause_name"]}"  (risk type: {e["code"]}). '
                   f'A federal court construed this provision.\n'
                   f'"""\n{flat(r["clause_text"])}\n"""')
        out.append(f"\nWhat the court said about it, verbatim from the opinion "
                   f"in {r['citation']}:\n\"\"\"\n{r['opinion_comment'].strip()}"
                   f"\n\"\"\"")
        if foil:
            out.append(f'\nLOWER RISK — "{foil["clause_name"]}", from the same '
                       f'contract. No court construed it in this case. That is '
                       f'the only thing known about it: it is not established to '
                       f'be sound, only never fought over.\n'
                       f'"""\n{flat(foil["clause_text"])}\n"""')
    return "\n".join(out)


def anonymise(clauses):
    """Present the provisions under opaque ids, in the order they appear.

    The dataset's `pos1`/`neg14` ids would put the gold label on the door of
    every provision and group the answers at the top of the list. So the model
    sees `c001`, `c002`, ... in `source_span` order — the sequence a reader
    meets them in, carrying no signal, each provision beside its neighbours,
    which is what a Category 2 judgement needs.

    The mapping is translated back before anything reaches the predictions file,
    so `clause_id` in the output still joins to `dataset.csv` unchanged.

    Returns (provisions in document order, real id -> opaque, opaque -> real).
    """
    order = sorted(clauses, key=lambda c: int(c["source_span"].split("-")[0]))
    real_of = {f"c{i:03d}": c["clause_id"] for i, c in enumerate(order, 1)}
    return order, {v: k for k, v in real_of.items()}, real_of


def clause_block(clauses, opaque_of):
    return "\n".join(
        f'\n[{opaque_of[c["clause_id"]]}] "{c["clause_name"]}"\n"""\n'
        f'{flat(c["clause_text"])}\n"""' for c in clauses)


# ------------------------------------------------------------------- run ----
TOP_UP_ROUNDS = 2


def top_up(cid, judged, real_of, fields):
    """Ask again, in the same conversation, for the provisions left out.

    61 judgments when 359 were asked for is not an answer, and scoring the rest
    as "not risky" would measure the harness. The follow-up carries the model's
    own previous answer as an assistant turn, so "these are missing" is checkable.
    Rounds stop as soon as one returns nothing new.
    """
    for _ in range(TOP_UP_ROUNDS):
        missing = [oid for oid in real_of if oid not in judged]
        if not missing:
            return judged
        print(f"    ~ {len(missing)} provision(s) unanswered, asking again")
        answer = lib.ask(
            "exp3", f"{cid}__topup{len(judged)}", effort="high",
            log_as="exp3_llm_api", more=[
                {"role": "assistant",
                 "content": json.dumps({"judgments": list(judged.values())},
                                       ensure_ascii=False)},
                {"role": "user", "content":
                    "That answer left out the following provision ids, which "
                    "were in the list you were asked to judge:\n\n"
                    + ", ".join(missing)
                    + "\n\nReturn judgments for EXACTLY these ids and no "
                      "others, in the same format. Do not repeat the ones you "
                      "already gave."},
            ], **fields)
        if not answer:
            return judged
        before = len(judged)
        for j in answer["judgments"]:
            oid = str(j["clause_id"])
            if oid in real_of and oid not in judged:
                judged[oid] = j
        gained = len(judged) - before
        print(f"      recovered {gained}, "
              f"{len(real_of) - len(judged)} still missing")
        if not gained:
            break
    return judged


def fill_gaps(groups, registry, block):
    """Finish contracts already scored but with provisions left unanswered.

    Runs before the main loop every time, so a gap is closed rather than
    inherited. Rows are appended with `ok=1`; readers prefer a scored row over a
    blank one for the same key, so earlier blanks are superseded and the file
    stays append-only.
    """
    if not PREDS.exists():
        return
    scored, blank = set(), set()
    for r in load_rows(PREDS):
        (scored if r["ok"] == "1" else blank).add((r["contract_id"], r["clause_id"]))
    gaps = defaultdict(set)
    for cid, clause_id in blank - scored:
        gaps[cid].add(clause_id)
    if not gaps:
        return
    print(f"{sum(len(v) for v in gaps.values())} provision(s) left unanswered by "
          f"an earlier run, in {len(gaps)} contract(s) — finishing those first")

    fout = open(PREDS, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(fout, fieldnames=FIELDS)
    for cid, ids in sorted(gaps.items()):
        clauses = groups[cid]
        raw = json.loads((RAW / f"{cid}.json").read_text(encoding="utf-8"))
        real_of = raw["_id_map"]
        judged = {str(j["clause_id"]): j for j in raw["judgments"]
                  if str(j.get("clause_id")) in real_of}
        text = (lib.ROOT / registry[cid]["file"]).read_text(encoding="utf-8")
        shown, opaque_of, _ = anonymise(clauses)
        fields = dict(citation=clauses[0]["citation"], contract_id=cid,
                      examples=block, document=text,
                      clauses=clause_block(shown, opaque_of))
        print(f"[{cid}] {len(ids)} missing of {len(clauses)}")
        judged = top_up(cid, judged, real_of, fields)

        gold = {c["clause_id"]: c for c in clauses}
        n = 0
        for c in clauses:
            j = judged.get(opaque_of[c["clause_id"]])
            if not j or (cid, c["clause_id"]) in scored:
                continue
            n += 1
            writer.writerow({
                "contract_id": cid, "citation": c["citation"],
                "clause_id": c["clause_id"], "clause_name": c["clause_name"],
                "label": c["label"], "taxonomy": c["taxonomy"],
                "gold": gold_of(c), "gold_subtype": gold_fine(c),
                "pred": pred_from_probs(_f(j.get("prob_cat1")),
                                        _f(j.get("prob_cat2"))),
                "prob_cat1": _f(j.get("prob_cat1")),
                "prob_cat2": _f(j.get("prob_cat2")),
                "reasoning_cat1": j.get("reasoning_cat1", ""),
                "reasoning_cat2": j.get("reasoning_cat2", ""),
                "ok": "1"})
        fout.flush()
        for j in judged.values():
            c = gold.get(real_of.get(str(j["clause_id"]), ""))
            if c:
                j["dataset_clause_id"] = c["clause_id"]
                j["gold"] = gold_of(c)
                j["gold_subtype"] = gold_fine(c)
                j["clause_name"] = c["clause_name"]
        (RAW / f"{cid}.json").write_text(json.dumps(
            {"judgments": list(judged.values()), "_id_map": real_of},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"    appended {n} newly judged provision(s)")
    fout.close()


def load_rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def by_contract(rows):
    """contract_id -> every clause of it, largest first so a bad call fails early."""
    g = defaultdict(list)
    for r in rows:
        g[r["contract_id"]].append(r)
    return OrderedDict(sorted(g.items(), key=lambda kv: -len(kv[1])))


def done_contracts(path, groups=None):
    """Contracts that need no further call.

    `groups` makes "done" mean COMPLETE. Without it one scored provision marks a
    contract finished, and a call that answered 1 of 85 is never revisited.
    """
    if not path.exists():
        return set()
    scored = defaultdict(set)
    for r in load_rows(path):
        if r["ok"] == "1":
            scored[r["contract_id"]].add(r["clause_id"])
    if groups is None:
        return set(scored)
    return {cid for cid, ids in scored.items()
            if len(ids) >= len(groups.get(cid, ()))}


def judge_one(cid, clauses, registry, block, n, total):
    """One contract: one call, plus top-ups. Runs in a worker thread."""
    text = (lib.ROOT / registry[cid]["file"]).read_text(encoding="utf-8")
    print(f"[{n}/{total}] {cid}: {len(clauses)} provisions, "
          f"{len(text):,}-char contract", flush=True)
    shown, opaque_of, real_of = anonymise(clauses)
    fields = dict(citation=clauses[0]["citation"], contract_id=cid,
                  examples=block, document=text,
                  clauses=clause_block(shown, opaque_of))
    answer = lib.ask("exp3", cid, effort="high", log_as="exp3_llm_api", **fields)
    if not answer:
        return {}, opaque_of

    judged = {str(j["clause_id"]): j for j in answer["judgments"]}
    judged = top_up(cid, judged, real_of, fields)
    gold = {c["clause_id"]: c for c in clauses}
    merged = list(judged.values())
    for j in merged:
        c = gold.get(real_of.get(str(j["clause_id"]), ""))
        if c:
            j["dataset_clause_id"] = c["clause_id"]
            j["gold"] = gold_of(c)
            j["gold_subtype"] = gold_fine(c)
            j["clause_name"] = c["clause_name"]
    (RAW / f"{cid}.json").write_text(json.dumps(
        {"judgments": merged, "_id_map": real_of},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return judged, opaque_of


def run(args):
    rows = load_rows(lib.OUT / "dataset.csv")
    examples = pick_examples(rows)
    taught = {e["row"]["contract_id"] for e in examples}
    print(f"examples ({len(examples)}): " + ", ".join(
        f"{e['code']}={e['row']['contract_id']}"
        f"({len(e['row']['opinion_comment'])}c opinion"
        f"{', +foil' if e['foil'] else ''})" for e in examples))

    eval_rows = [r for r in rows if r["contract_id"] not in taught]
    groups = by_contract(eval_rows)
    block = example_block(examples)
    registry = lib.read_json(lib.OUT / "contracts.json", {})

    done = done_contracts(PREDS, groups)
    todo = [(cid, cl) for cid, cl in groups.items() if cid not in done]

    # `--sample` draws contracts at random; `--limit` takes the ones with the
    # most provisions. They answer different questions. A sample is what you
    # want for a pilot whose numbers should generalise — taking the largest
    # contracts biases towards long insurance policies, which are not typical of
    # the corpus. `--limit` is for stress-testing the budgets.
    #
    # The draw is over ALL eligible contracts, not just the unscored ones, so
    # the seed alone reproduces the sample no matter what has been run before.
    # Anything already scored is then skipped, which means `--sample 12` can
    # launch fewer than twelve calls — that is the point: the sample is a fixed
    # set of contracts, not a fixed amount of work.
    if args.sample:
        pick = set(random.Random(args.seed).sample(
            sorted(groups), min(args.sample, len(groups))))
        todo = [(cid, cl) for cid, cl in todo if cid in pick]
        print(f"sample of {len(pick)} contract(s), seed {args.seed}; "
              f"{len(todo)} of them not yet scored:")
        for cid, cl in todo:
            print(f"    {cid[:56]:58} {len(cl):4d} provisions")
    if args.limit:
        todo = todo[:args.limit]

    # Size order is the default because it fails early: the contract most likely
    # to overrun MAX_OUTPUT is the one with the most provisions, and finding that
    # out on call 1 costs one call rather than a whole run.
    #
    # `--shuffle` trades that away deliberately. A run watched from the outside
    # is judged on its first N contracts, and under size order those N are the
    # longest documents in the corpus — mostly insurance policies. Any metric
    # read off them is a metric for insurance policies. Shuffling makes the
    # prefix a fair sample of the corpus, at the cost of meeting the largest
    # contract somewhere in the middle.
    if args.shuffle:
        random.Random(args.seed).shuffle(todo)
        print(f"order shuffled, seed {args.seed}")

    n_pos = sum(1 for r in eval_rows if r["label"] == "POSITIVE")
    print(f"{len(eval_rows)} clauses ({n_pos} risky) in {len(groups)} contracts "
          f"after excluding {len(taught)} example contract(s)")
    print(f"{len(done)} contract(s) done, {len(todo)} to run "
          f"(one call each, model {lib.MODEL}, effort high, "
          f"max_tokens {lib.MAX_OUTPUT:,})")

    if args.dry_run:
        est(todo, registry, block)
        return
    RAW.mkdir(parents=True, exist_ok=True)
    fill_gaps(groups, registry, block)
    if not todo:
        metrics()
        return

    new = not PREDS.exists()
    fout = open(PREDS, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(fout, fieldnames=FIELDS)
    if new:
        writer.writeheader()

    # Contracts run in threads; only the main thread writes the CSV. The calls
    # are independent, so a contract that fails is simply not written and comes
    # back as outstanding on the next run.
    lib.client()          # built once here, not raced for by the workers
    with cf.ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futs = {pool.submit(judge_one, cid, cl, registry, block, i, len(todo)):
                (cid, cl) for i, (cid, cl) in enumerate(todo, 1)}
        for fut in cf.as_completed(futs):
            cid, clauses = futs[fut]
            try:
                judged, opaque_of = fut.result()
            except Exception as e:
                print(f"    ! {cid}: {type(e).__name__}: {str(e)[:200]}",
                      flush=True)
                continue

            n_ok = 0
            for c in clauses:
                j = judged.get(opaque_of[c["clause_id"]])
                ok = j is not None
                n_ok += ok
                p1 = _f(j.get("prob_cat1")) if ok else ""
                p2 = _f(j.get("prob_cat2")) if ok else ""
                writer.writerow({
                    "contract_id": cid, "citation": c["citation"],
                    "clause_id": c["clause_id"], "clause_name": c["clause_name"],
                    "label": c["label"], "taxonomy": c["taxonomy"],
                    "gold": gold_of(c), "gold_subtype": gold_fine(c),
                    "pred": pred_from_probs(p1, p2) if ok else "",
                    "prob_cat1": p1, "prob_cat2": p2,
                    "reasoning_cat1": j.get("reasoning_cat1", "") if ok else "",
                    "reasoning_cat2": j.get("reasoning_cat2", "") if ok else "",
                    "ok": "1" if ok else "0"})
            fout.flush()
            missing = len(clauses) - n_ok
            print(f"    {cid}: {n_ok}/{len(clauses)} judged"
                  + (f"  ({missing} MISSING)" if missing else ""), flush=True)

    fout.close()
    metrics()


def est(todo, registry, block):
    """What the outstanding calls would cost, before any is sent.

    The input side is exact: every prompt is BUILT as `lib.ask` would build it,
    the largest metered by Claude's own counter, the rest scaled from its
    characters-per-token. The output side cannot be known in advance, so three
    rates are shown rather than a forecast. It also checks the largest
    contract's judgments against lib.MAX_OUTPUT — a call that overruns is lost,
    not truncated.
    """
    if not todo:
        print("\nnothing outstanding — no calls to price")
        return

    built = []
    for cid, cl in todo:
        text = (lib.ROOT / registry[cid]["file"]).read_text(encoding="utf-8")
        shown, opaque_of, _ = anonymise(cl)
        p = lib.prompt("exp3", citation=cl[0]["citation"], contract_id=cid,
                       examples=block, document=text,
                       clauses=clause_block(shown, opaque_of))
        built.append((cid, sum(len(v) for v in p.values()), len(cl), p))

    big = max(built, key=lambda b: b[1])
    cpt = big[1] / lib.n_tokens("\n".join(big[3].values()))
    tin = sum(b[1] for b in built) / cpt / 1e6
    n_clauses = sum(len(cl) for _c, cl in todo)
    most = max(built, key=lambda b: b[2])

    print(f"\n{len(todo)} call(s), one per contract, {n_clauses:,} provisions")
    print(f"  {cpt:.2f} chars per token, measured on the largest prompt")
    print(f"  largest input:  {big[1] / cpt:,.0f} tokens in {big[0]}  "
          f"(ceiling {lib.MAX_INPUT_TOKENS:,})")
    print(f"  most provisions in one call: {most[2]} in {most[0]}")
    print(f"\n  input   {tin:.2f}M tokens  @ $5/M  =  ${tin * 5:6.2f}")
    for per in (110, 190, 320):
        tout = n_clauses * per / 1e6
        worst = most[2] * per
        fit = "" if worst < lib.MAX_OUTPUT else \
            f"  <-- {worst:,} > MAX_OUTPUT, {most[0]} would be LOST"
        print(f"  output  {tout:.2f}M ({per:3d} tok/judgment) @ $25/M  "
              f"=  ${tout * 25:6.2f}    TOTAL ${tin * 5 + tout * 25:6.2f}"
              f"{fit}")


# --------------------------------------------------------------- metrics ----
def roc_auc(scores, labels):
    """Rank-based ROC-AUC (Mann-Whitney U), ties averaged."""
    if not any(labels) or all(labels):
        return float("nan")
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j < len(order) and scores[order[j]] == scores[order[i]]:
            j += 1
        for k in range(i, j):
            ranks[order[k]] = (i + j - 1) / 2.0 + 1.0
        i = j
    n_pos = sum(labels)
    n_neg = len(labels) - n_pos
    s = sum(r for r, y in zip(ranks, labels) if y)
    return (s - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def pr_auc(scores, labels):
    """Average precision."""
    if not any(labels):
        return float("nan")
    order = sorted(range(len(scores)), key=lambda i: -scores[i])
    tp = fp = 0
    ap = prev = 0.0
    total = sum(labels)
    for i in order:
        tp, fp = (tp + 1, fp) if labels[i] else (tp, fp + 1)
        rec = tp / total
        ap += tp / (tp + fp) * (rec - prev)
        prev = rec
    return ap


def metrics():
    if not PREDS.exists():
        print("no predictions yet")
        return
    # A clause the model never returned is not dropped: silence is scored as a
    # prediction of "not risky" at probability 0. Dropping it would quietly
    # forgive the omission -- and if the clause is a positive, inflate recall.
    keep = {}
    for r in load_rows(PREDS):
        k = (r["contract_id"], r["clause_id"])
        prev = keep.get(k)
        if prev is not None and (prev["ok"] == "1" or r["ok"] != "1"):
            continue                      # never let a blank mask a real score
        keep[k] = r
    rows = list(keep.values())
    if not rows:
        print("no predictions yet")
        return
    unscored = 0
    for r in rows:
        if r["ok"] != "1":
            unscored += 1
            r["pred"], r["prob_cat1"], r["prob_cat2"] = "not_risky", "0", "0"
    if unscored:
        n_pos = sum(1 for r in rows if r["ok"] != "1" and r["gold"] != "not_risky")
        print(f"! {unscored} clause(s) returned no judgment ({n_pos} positive)"
              f" -- scored as not_risky")

    tp = defaultdict(int)
    fp = defaultdict(int)
    fn = defaultdict(int)
    support = defaultdict(int)
    conf = {g: defaultdict(int) for g in COARSE}
    for r in rows:
        g, p = r["gold"], r["pred"]
        support[g] += 1
        conf[g][p] += 1
        for c in COARSE:
            if g == c and p == c:
                tp[c] += 1
            elif p == c:
                fp[c] += 1
            elif g == c:
                fn[c] += 1

    def prf(t, f, n):
        pr = t / (t + f) if t + f else 0.0
        rc = t / (t + n) if t + n else 0.0
        return pr, rc, (2 * pr * rc / (pr + rc) if pr + rc else 0.0)

    print(f"\n=== 3-way ({len(rows)} clauses) ===")
    print(f"{'class':11}{'P':>7}{'R':>7}{'F1':>7}{'TP':>6}{'FP':>6}{'FN':>6}{'supp':>7}")
    f1s = []
    for c in COARSE:
        pr, rc, f1 = prf(tp[c], fp[c], fn[c])
        f1s.append(f1)
        print(f"{c:11}{pr:7.2f}{rc:7.2f}{f1:7.2f}{tp[c]:6}{fp[c]:6}{fn[c]:6}"
              f"{support[c]:7}")
    print(f"\nmacro-F1 {sum(f1s) / len(f1s):.3f}   "
          f"accuracy {sum(tp.values()) / len(rows):.3f}")

    scores = [max(_f(r["prob_cat1"]), _f(r["prob_cat2"])) for r in rows]
    labels = [1 if r["gold"] != "not_risky" else 0 for r in rows]
    b_tp = sum(1 for r, y in zip(rows, labels) if y and r["pred"] != "not_risky")
    b_fl = sum(1 for r in rows if r["pred"] != "not_risky")
    pr, rc, f1 = prf(b_tp, b_fl - b_tp, sum(labels) - b_tp)
    print(f"\nbinary risky-vs-not @{FLAG}:  P={pr:.2f} R={rc:.2f} F1={f1:.2f}  "
          f"({b_tp}/{sum(labels)} caught, {b_fl} flagged = "
          f"{100 * b_fl / len(rows):.0f}%)")
    print(f"binary risky-vs-not ranked: ROC-AUC={roc_auc(scores, labels):.3f}  "
          f"PR-AUC={pr_auc(scores, labels):.3f}  "
          f"(prevalence {sum(labels) / len(labels):.3f})")
    for cat, col in (("cat1", "prob_cat1"), ("cat2", "prob_cat2")):
        sc = [_f(r[col]) for r in rows]
        lb = [1 if r["gold"] == f"risky_{cat}" else 0 for r in rows]
        print(f"  {col}: ROC-AUC={roc_auc(sc, lb):.3f}  "
              f"PR-AUC={pr_auc(sc, lb):.3f}  (support {sum(lb)})")

    print("\nconfusion (rows gold, cols pred):")
    print(f"{'':13}" + "".join(f"{p:>13}" for p in COARSE))
    for g in COARSE:
        print(f"{g:13}" + "".join(f"{conf[g][p]:>13}" for p in COARSE))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="run only the N contracts with the most provisions")
    ap.add_argument("--sample", type=int, default=0,
                    help="run a random N of all eligible contracts (seed alone "
                         "fixes the draw); already-scored ones are skipped")
    ap.add_argument("--shuffle", action="store_true",
                    help="run the contracts in a seeded random order instead of "
                         "largest first, so the first N are a fair sample")
    ap.add_argument("--seed", type=int, default=0, help="sample and shuffle seed")
    ap.add_argument("--parallel", type=int, default=1,
                    help="contracts in flight at once (one call each)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the cost of the outstanding calls and stop")
    ap.add_argument("--metrics-only", action="store_true")
    args = ap.parse_args()

    if args.metrics_only:
        metrics()
        return
    run(args)


if __name__ == "__main__":
    main()

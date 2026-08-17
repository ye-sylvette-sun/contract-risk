"""Experiment 3, agentic: one Claude Code session per contract.

The judging task is identical to `exp3_llm_api.py` — same taxonomy, same worked
examples, same probabilities, same output schema, same gold. Only the DELIVERY
differs, and that is the whole point of running both:

  * `exp3_llm_api.py` puts the contract, the examples and every provision into
    one prompt and asks for the answer in one shot. The model reads what it is
    given, once, in the order given.
  * this script gives the model a WORKSPACE and lets it decide what to read.
    The contract is a file. Each example contract is a file. The provisions to
    judge are a file. The model greps, re-reads, jumps back to a cross-reference
    it half-remembers, and writes its answers to a file.

Category 2 is the reason to expect a difference. "Does this provision conflict
with another one?" is a search problem over a 300,000-character document, and
the one-shot version has to answer it from a single reading. An agent can go and
look.

The workspace, one directory per contract:

    contract.txt              the contract under judgment, verbatim
    provisions.json           what to judge: opaque id, name, verbatim text
    examples/<code>/          one worked pair per risk type
        contract.txt              the full contract the example came from
        notes.md                  which provision the court construed, the
                                  court's own words, the lower-risk foil, and
                                  how many of that contract's provisions were
                                  construed at all
    predictions.json          written by the agent — this is the deliverable

No gold reaches the workspace of the contract being judged. `provisions.json`
carries opaque ids in document order (see `anonymise`), never `pos1`/`neg1`, and
the answers are mapped back here.

Auth: this runs on the Claude Code SUBSCRIPTION, not the API key. See `env()`.

Input : output/dataset.csv, output/contracts.json
Output: output/exp3_agent_preds.csv      one row per provision, same schema
        output/exp3_agent/<cid>.json     the agent's returned judgments
        output/exp3_agent_ws/<cid>/      the workspace it worked in
        output/llm_logs/exp3_agent/<cid>.json   session cost, usage, turns

Usage:
    python src/experiments/exp3_agent.py --limit 1      # pilot, measure cost
    python src/experiments/exp3_agent.py --shuffle      # the full run
"""
import argparse
import asyncio
import csv
import importlib.util
import json
import os
import random
import re
import shutil
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib  # noqa: E402

# Everything about WHAT to judge is shared with the one-shot experiment by
# importing it, not by copying it. If the taxonomy, the example choice or the
# gold mapping ever drift between the two scripts the comparison is worthless,
# and a copy is exactly how that happens.
_spec = importlib.util.spec_from_file_location(
    "exp3_llm_api", Path(__file__).with_name("exp3_llm_api.py"))
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)

MODEL = "claude-opus-5"
EFFORT = "high"
MAX_TURNS = 60          # a 400-provision contract needs room to read and re-read

PREDS = lib.OUT / "exp3_agent_preds.csv"
RAW = lib.OUT / "exp3_agent"
WS = lib.OUT / "exp3_agent_ws"
LOGS = lib.OUT / "llm_logs" / "exp3_agent"

REST_SECONDS = 1 * 60 * 60  # the blind fallback's pause, see `--rest-every`
WARN_AT = 0.90              # utilisation at which we stop starting new sessions


# ------------------------------------------------------------------- auth ----
def env():
    """The environment the CLI runs in — subscription billing, caching on.

    The SDK spawns the Claude Code CLI with `env=os.environ`, inherited whole.
    `lib` loads `.env`, which carries `ANTHROPIC_API_KEY`, and the CLI bills the
    API whenever it finds that key. Popping it is what makes this run count
    against the subscription instead. Nothing here needs the key: the agent path
    never calls the Messages API directly.

    `DISABLE_PROMPT_CACHING` is cleared for the same reason in reverse. Caching
    is on by default and matters far more here than in the one-shot experiment:
    an agent re-sends its whole transcript on every turn, so the contract it read
    on turn 2 is re-billed on turns 3..60 unless the cache absorbs it.
    """
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "DISABLE_PROMPT_CACHING"):
        os.environ.pop(k, None)


# -------------------------------------------------------------- workspace ----
def write_examples(root, examples):
    """One directory per worked pair: the whole contract, plus the notes."""
    for e in examples:
        r, foil = e["row"], e["foil"]
        d = root / "examples" / f"{e['code']}_{r['contract_id']}"
        d.mkdir(parents=True, exist_ok=True)
        src = lib.ROOT / r["contract_file"]
        shutil.copyfile(src, d / "contract.txt")

        notes = [
            f"# Worked example — {api.TYPE_NAME[e['code']]}",
            "",
            f"Contract `{r['contract_id']}`, filed in {r['citation']}. The full "
            f"text is in `contract.txt` beside this file — read it if you want "
            f"to see what the construed provision sits next to.",
            "",
            f"A court construed **{e['n_pos']}** of this contract's provisions; "
            f"the other **{e['n_neg']}** it did not. That ratio is what a real "
            f"contract looks like. Read it as the base rate to expect, NOT as a "
            f"quota to reproduce.",
            "",
            "## HIGH RISK — a federal court construed this provision",
            "",
            f'"{r["clause_name"]}"  (risk type: {e["code"]})',
            "",
            "```", api.flat(r["clause_text"]), "```",
            "",
            f"### What the court said about it, verbatim from the opinion in "
            f"{r['citation']}",
            "",
            "```", r["opinion_comment"].strip(), "```",
        ]
        if foil:
            notes += [
                "",
                "## LOWER RISK — from the same contract",
                "",
                f'"{foil["clause_name"]}". No court construed it in this case. '
                f"That is the only thing known about it: it is not established "
                f"to be sound, only never fought over.",
                "",
                "```", api.flat(foil["clause_text"]), "```",
            ]
        (d / "notes.md").write_text("\n".join(notes), encoding="utf-8")


def build_workspace(cid, clauses, registry, examples):
    """Lay out one contract's workspace. Returns (dir, real_of)."""
    root = WS / cid
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    shutil.copyfile(lib.ROOT / registry[cid]["file"], root / "contract.txt")
    write_examples(root, examples)

    shown, _opaque_of, real_of = api.anonymise(clauses)
    (root / "provisions.json").write_text(json.dumps(
        [{"id": oid, "name": c["clause_name"], "text": api.flat(c["clause_text"])}
         for oid, c in zip(real_of, shown)],
        ensure_ascii=False, indent=2), encoding="utf-8")
    return root, real_of


def task_prompt(cid, citation, n, examples):
    """What to do, given the workspace. The judging criteria are NOT here.

    They are in the system prompt, taken verbatim from `prompts/exp3.md`, so the
    two experiments are asked the same question in the same words.
    """
    dirs = "\n".join(
        f"    examples/{e['code']}_{e['row']['contract_id']}/" for e in examples)
    return f"""You are judging contract `{cid}`, filed in {citation}.

Your working directory holds everything you need.

    contract.txt          the contract to judge, in full
    provisions.json       the {n} provisions to judge, in the order they appear
                          in the contract, each with an id like `c001`
    examples/             one worked pair per risk type — read these FIRST
{dirs}

Work in this order.

1. Read every `examples/*/notes.md`. Each gives a provision a federal court
   actually construed and the court's own words about the dispute, paired with a
   provision from the same contract that no court construed. That is the
   standard to apply — not your own sense of what looks badly drafted. The
   example contracts are there in full if you want the context around them.
2. Read `contract.txt`. You need the whole instrument for Category 2: a conflict
   cannot be seen from one provision alone. It is long — read it in pieces, and
   use Grep to chase a defined term or a cross-reference wherever it leads.
3. Read `provisions.json` and judge every provision in it.

For each provision decide two INDEPENDENT probabilities, each to two decimal
places on a 0.01 grid (0.03, 0.17, 0.62 — do not round to the nearest 0.05):

  * `prob_cat1` — that it carries a Category 1 risk (an intrinsic textual
    defect, visible in the provision itself)
  * `prob_cat2` — that it carries a Category 2 risk (a defect in its
    relationship to the rest of the instrument)

Take the second one seriously: you can search this contract, so go and check.
Name the other provision you checked against.

Write your answers to `predictions.json` in the working directory, as an object:

    {{"judgments": [
      {{"clause_id": "c001",
        "reasoning_cat1": "two sentences on the provision's own wording",
        "reasoning_cat2": "two sentences on its fit with the rest of the contract",
        "prob_cat1": 0.07,
        "prob_cat2": 0.03}},
      ...
    ]}}

Rules for that file:

  * one entry for EVERY id in `provisions.json`, all {n} of them, same order
  * `clause_id` copied exactly — `c001`, not the provision's heading
  * keep each reasoning to two sentences; you are producing a judgment, not a
    memorandum
  * write the file even if you are unsure about some provisions; a missing entry
    is scored as "not risky" at probability 0, which is the worst outcome
    available to you — an entry you are unsure of is always better than none

**Work in batches and flush as you go.** Do not hold {n} judgments in your head
and write them all at the end: if the session ends early, everything unwritten
is lost and scored against you. Instead judge fifty or so provisions, write them
out, then continue with the next batch. Write each batch to its OWN numbered
file — `predictions_001.json`, `predictions_002.json`, and so on, each with the
same `judgments` shape. They are read together and concatenated, so never
rewrite an earlier file and never repeat an id.

When every provision in `provisions.json` has been written to one of those
files, stop. Reply with one line: how many judgments you wrote.
"""


# ------------------------------------------------------------------- run -----
FIELD_RE = {
    "clause_id": re.compile(r'"clause_id"\s*:\s*"(.*?)"\s*,', re.S),
    "reasoning_cat1": re.compile(
        r'"reasoning_cat1"\s*:\s*"(.*?)"\s*,\s*"(?:reasoning_cat2|prob_)', re.S),
    "reasoning_cat2": re.compile(
        r'"reasoning_cat2"\s*:\s*"(.*?)"\s*,\s*"prob_', re.S),
    "prob_cat1": re.compile(r'"prob_cat1"\s*:\s*([0-9.]+)'),
    "prob_cat2": re.compile(r'"prob_cat2"\s*:\s*([0-9.]+)'),
}


def salvage(text):
    """The judgments in one file, whether or not the file is valid JSON.

    The agent writes these files by hand, and on legal text it eventually writes
    one that does not parse: it quotes the contract verbatim inside a reasoning
    string — `the term "profit" shall be understood to mean` — without escaping
    the inner quotes. One such provision made `json.loads` reject the whole
    file, and 75 judgments that had been paid for and were sitting on disk were
    read as absent.

    So a failed parse falls back to reading the objects one at a time, and an
    object that still will not parse is read field by field. The field patterns
    anchor on the NEXT key rather than on a closing quote, which is precisely
    what makes them immune to unescaped quotes inside a value.

    Nothing here invents data: a field that cannot be found is not supplied, and
    a judgment without both probabilities is dropped by the caller.
    """
    try:
        data = json.loads(text)
        j = data.get("judgments") if isinstance(data, dict) else data
        if isinstance(j, list):
            return j, None
        return [], "no judgments list"
    except json.JSONDecodeError:
        pass

    # Split on the start of each judgment, which the schema pins down exactly.
    starts = [m.start() for m in re.finditer(r'\{\s*"clause_id"', text)]
    out, repaired = [], 0
    for i, s in enumerate(starts):
        chunk = text[s:starts[i + 1] if i + 1 < len(starts) else len(text)]
        try:
            out.append(json.loads(chunk.rstrip().rstrip(",").rstrip("]}").rstrip()
                                  if not chunk.rstrip().endswith("}") else chunk))
            continue
        except json.JSONDecodeError:
            pass
        got = {}
        for key, rx in FIELD_RE.items():
            m = rx.search(chunk)
            if m:
                got[key] = m.group(1)
        if "clause_id" in got and "prob_cat1" in got and "prob_cat2" in got:
            out.append(got)
            repaired += 1
    note = (f"invalid JSON; recovered {len(out)} judgment(s) object-by-object"
            + (f", {repaired} field-by-field" if repaired else ""))
    return out, note


def read_predictions(root, real_of, clauses):
    """The agent's file -> {dataset clause_id: judgment}. Tolerant by design.

    A malformed entry is dropped rather than allowed to abort the contract: the
    session has already been paid for, and one unparseable provision out of four
    hundred should cost one provision.
    """
    files = sorted(root.glob("predictions*.json"))
    if not files:
        return {}, "no predictions file"

    by_real, bad = {}, []
    for f in files:
        judgments, note = salvage(f.read_text(encoding="utf-8"))
        if note:
            bad.append(f"{f.name}: {note}")
        for j in judgments:
            if not isinstance(j, dict):
                continue
            # An id we never issued is dropped rather than guessed at. The
            # agent sometimes appends a sentinel row of its own invention; that
            # is exactly what this catches.
            real = real_of.get(str(j.get("clause_id", "")).strip())
            if real is None or "prob_cat1" not in j or "prob_cat2" not in j:
                continue
            by_real[real] = j

    short = len(by_real) < len(clauses)
    why = "; ".join(bad) if bad else None
    if short and not why:
        why = (f"only {len(by_real)} of {len(clauses)} provisions judged across "
               f"{len(files)} file(s)")
    return by_real, why


async def judge(cid, root, prompt_text, system_prompt, limits, resume=None):
    """One agent session. `resume` continues an earlier one by its id."""
    from claude_agent_sdk import (ClaudeAgentOptions, RateLimitEvent,
                                  ResultMessage, query)

    options = ClaudeAgentOptions(
        model=MODEL,
        effort=EFFORT,
        cwd=str(root),
        resume=resume,
        system_prompt=system_prompt,
        # Read/Grep/Glob to explore, Write to answer. No Bash and no Edit: the
        # agent has no reason to run commands or alter the contract it is
        # judging, and a tool it cannot call is a failure mode it cannot have.
        tools=["Read", "Grep", "Glob", "Write"],
        allowed_tools=["Read", "Grep", "Glob", "Write"],
        permission_mode="acceptEdits",
        max_turns=MAX_TURNS,
        # Hermetic: no user or project settings, so the run does not depend on
        # whatever is in ~/.claude at the time.
        setting_sources=None,
    )

    result = {}
    async for message in query(prompt=prompt_text, options=options):
        if isinstance(message, RateLimitEvent):
            info = message.rate_limit_info
            limits[info.rate_limit_type or "unknown"] = info
            print(f"    [rate limit] {info.rate_limit_type}: {info.status}"
                  + (f", {info.utilization:.0%} used" if info.utilization is not None else "")
                  + (f", resets {stamp(info.resets_at)}" if info.resets_at else ""))
        elif isinstance(message, ResultMessage):
            result = {
                "is_error": message.is_error,
                "stop_reason": message.stop_reason,
                "num_turns": message.num_turns,
                "duration_ms": message.duration_ms,
                "total_cost_usd": message.total_cost_usd,
                "usage": message.usage,
                "model_usage": message.model_usage,
                "session_id": message.session_id,
                "errors": message.errors,
                "result": (message.result or "")[:500],
            }
    return result


TOP_UP_ROUNDS = 2


def fill_prompt(missing):
    """Tell the session, in its own context, which ids it still owes."""
    return (
        f"You have not written judgments for these {len(missing)} provision "
        f"ids from `provisions.json`:\n\n" + ", ".join(missing) + "\n\n"
        "Judge EXACTLY these and no others. Re-read the parts of "
        "`contract.txt` they sit in if you need to. Write them to "
        "`predictions_fill_001.json` (and `_002`, `_003` … if you work in "
        "batches) in the same shape as your earlier files — do not modify a "
        "file you have already written. Probabilities to two decimal places on "
        "a 0.01 grid. Reply with the count when every id above is written."
    )


async def top_up(cid, root, real_of, clauses, judged, system_prompt, limits,
                 session_id):
    """Resume the session and have it finish what it left out.

    A session that runs out of room stops writing. It does not say so, and it
    exits cleanly, so nothing upstream retries it — on `774FSupp2d1073` that
    silently cost 50 provisions of 359. Scoring those as "not risky" would
    measure this harness rather than the model, so it is asked to finish.

    Resuming, rather than starting fresh, is what makes the request meaningful:
    the session still holds what it read and what it already answered, so
    "these ids are missing" is something it can check. The CLI compacts the
    restored context, which is also why the second attempt has room where the
    first ran out.
    """
    opaque_of = {v: k for k, v in real_of.items()}
    res = {}
    for r in range(TOP_UP_ROUNDS):
        missing = [opaque_of[c["clause_id"]] for c in clauses
                   if c["clause_id"] not in judged]
        if not missing:
            return judged, res
        print(f"    ~ {len(missing)} provision(s) unwritten, resuming session "
              f"(round {r + 1}/{TOP_UP_ROUNDS})")
        try:
            res = await judge(cid, root, fill_prompt(missing), system_prompt,
                              limits, resume=session_id)
            session_id = res.get("session_id") or session_id
        except Exception as e:                                   # noqa: BLE001
            print(f"    ! top-up raised {type(e).__name__}: {str(e)[:120]}")
            res = {"is_error": True, "errors": [f"{type(e).__name__}: {e}"]}

        before = len(judged)
        judged, _ = read_predictions(root, real_of, clauses)
        gained = len(judged) - before
        print(f"      recovered {gained}, {len(clauses) - len(judged)} still missing")
        if gained == 0:
            return judged, res       # no progress; another round would not help
    return judged, res


def stamp(unix):
    if not unix:
        return "?"
    return datetime.fromtimestamp(unix, timezone.utc).astimezone().strftime("%H:%M")


def pause_for(limits):
    """Seconds to wait before starting another session, from what the CLI said.

    The CLI emits a rate-limit event only when the state CHANGES, so silence is
    not evidence of headroom — it usually means nothing has moved since the last
    event. This reads whatever the most recent events said and is deliberately
    conservative: a hard `rejected` sleeps until the stated reset, and merely
    being close to the ceiling stops the run rather than creeping over it.
    """
    for kind, info in limits.items():
        if info.status == "rejected":
            wait = max(0, (info.resets_at or 0) - time.time()) + 60
            return wait, f"{kind} limit reached, sleeping until {stamp(info.resets_at)}"
        if info.utilization is not None and info.utilization >= WARN_AT:
            wait = max(0, (info.resets_at or 0) - time.time()) + 60
            return wait, (f"{kind} at {info.utilization:.0%}, waiting for the "
                          f"window to reset at {stamp(info.resets_at)}")
    return 0, None


def informative(limits):
    """Whether anything we have been told actually measures the headroom.

    A `five_hour: allowed` event with no `utilization` says only that the last
    call was permitted — it does not say how close the ceiling is, and treating
    its arrival as knowledge would silently disable the blind pacing fallback.
    Only a utilisation figure, or an outright rejection, counts.
    """
    return any(i.utilization is not None or i.status == "rejected"
               for i in limits.values())


async def fill_gaps(by_cid, examples, system_prompt, limits):
    """Finish contracts already scored, but with provisions left unjudged.

    Runs before the main loop, every time, so a gap left by an earlier run is
    closed rather than inherited. It appends rows with `ok=1`; readers prefer a
    scored row over a blank one for the same (contract_id, clause_id), so the
    earlier blanks are superseded without editing anything, and the predictions
    file stays append-only.
    """
    if not PREDS.exists():
        return
    scored, blank = set(), set()
    for r in api.load_rows(PREDS):
        (scored if r["ok"] == "1" else blank).add((r["contract_id"], r["clause_id"]))
    gaps = defaultdict(set)
    for cid, clause_id in blank - scored:
        gaps[cid].add(clause_id)
    if not gaps:
        return
    print(f"{sum(len(v) for v in gaps.values())} provision(s) left unjudged by "
          f"an earlier run, in {len(gaps)} contract(s) — finishing those first")

    fout = open(PREDS, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(fout, fieldnames=api.FIELDS)
    for cid, ids in sorted(gaps.items()):
        clauses = by_cid[cid]
        root = WS / cid
        real_of = json.loads((RAW / f"{cid}.json").read_text(encoding="utf-8"))["_id_map"]
        if not root.exists():
            registry = lib.read_json(lib.OUT / "contracts.json", {})
            root, real_of = build_workspace(cid, clauses, registry, examples)
        judged, _ = read_predictions(root, real_of, clauses)
        sid = json.loads((LOGS / f"{cid}.json").read_text(
            encoding="utf-8")).get("session_id")
        print(f"[{cid}] {len(ids)} missing of {len(clauses)}")
        judged, _ = await top_up(cid, root, real_of, clauses, judged,
                                 system_prompt, limits, sid)

        n = 0
        for c in clauses:
            j = judged.get(c["clause_id"])
            if not j or (cid, c["clause_id"]) in scored:
                continue
            n += 1
            writer.writerow({
                "contract_id": cid, "citation": c["citation"],
                "clause_id": c["clause_id"], "clause_name": c["clause_name"],
                "label": c["label"], "taxonomy": c["taxonomy"],
                "gold": api.gold_of(c), "gold_subtype": api.gold_fine(c),
                "pred": api.pred_from_probs(api._f(j.get("prob_cat1")),
                                            api._f(j.get("prob_cat2"))),
                "prob_cat1": api._f(j.get("prob_cat1")),
                "prob_cat2": api._f(j.get("prob_cat2")),
                "reasoning_cat1": j.get("reasoning_cat1", ""),
                "reasoning_cat2": j.get("reasoning_cat2", ""),
                "ok": "1"})
        fout.flush()
        (RAW / f"{cid}.json").write_text(json.dumps({
            "contract_id": cid, "_id_map": real_of,
            "judgments": [dict(j, dataset_clause_id=k) for k, j in judged.items()],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"    appended {n} newly judged provision(s)")
    fout.close()


async def run(args):
    rows = api.load_rows(lib.OUT / "dataset.csv")
    registry = lib.read_json(lib.OUT / "contracts.json", {})
    examples = api.pick_examples(rows)
    taught = {e["row"]["contract_id"] for e in examples}
    print("examples (%d): " % len(examples) + ", ".join(
        f"{e['code']}={e['row']['contract_id']}({e['n_pos']}p/{e['n_neg']}n)"
        for e in examples))

    eval_rows = [r for r in rows if r["contract_id"] not in taught]
    groups = api.by_contract(eval_rows)
    done = api.done_contracts(PREDS)
    todo = [(cid, cl) for cid, cl in groups.items() if cid not in done]
    if args.shuffle:
        random.Random(args.seed).shuffle(todo)
        print(f"order shuffled, seed {args.seed}")
    if args.limit:
        todo = todo[:args.limit]

    n_pos = sum(1 for r in eval_rows if r["label"] == "POSITIVE")
    print(f"{len(eval_rows)} clauses ({n_pos} risky) in {len(groups)} contracts "
          f"after excluding {len(taught)} example contract(s)")
    print(f"{len(done)} contract(s) done, {len(todo)} to run "
          f"(one agent session each, {MODEL}, effort {EFFORT}, "
          f"max {MAX_TURNS} turns, subscription billing)")

    # The judging criteria, word for word from the one-shot experiment. The
    # placeholders are filled with empty strings because only SYSTEM is wanted;
    # taking it from the same file is what keeps the two comparable.
    system_prompt = lib.prompt("exp3", examples="", contract_id="", citation="",
                               document="", clauses="")["SYSTEM"]

    for d in (RAW, LOGS, WS):
        d.mkdir(parents=True, exist_ok=True)

    limits = {}
    await fill_gaps(groups, examples, system_prompt, limits)
    if not todo:
        return

    new = not PREDS.exists()
    fout = open(PREDS, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(fout, fieldnames=api.FIELDS)
    if new:
        writer.writeheader()

    spend, since_rest = 0.0, 0
    for i, (cid, clauses) in enumerate(todo, 1):
        wait, why = pause_for(limits)
        if wait > 0:
            print(f"  ~ {why} ({wait / 3600:.1f}h)")
            time.sleep(wait)
            limits.clear()
        elif args.rest_every and since_rest >= args.rest_every \
                and not informative(limits):
            # Nothing has ever told us where we stand, so fall back to the blind
            # pacing rule rather than assume there is room.
            print(f"  ~ no usable rate-limit reading from the CLI; resting "
                  f"{REST_SECONDS / 3600:.0f}h after {since_rest} contracts")
            time.sleep(REST_SECONDS)
            since_rest = 0

        root, real_of = build_workspace(cid, clauses, registry, examples)
        print(f"[{i}/{len(todo)}] {cid}: {len(clauses)} provisions, "
              f"{(root / 'contract.txt').stat().st_size:,}-char contract")

        prompt_text = task_prompt(cid, clauses[0]["citation"], len(clauses),
                                  examples)
        # One contract must never be able to end the run. The SDK raises when
        # the CLI exits non-zero — including cases where the reported text is
        # useless ("success") — and on a 64-session run that is close to certain
        # to happen at least once. A failed contract is recorded with `ok=0`
        # rows, which keeps it OUT of `done_contracts` so a later run retries it.
        result = {}
        for attempt in range(2):
            try:
                result = await judge(cid, root, prompt_text, system_prompt, limits)
                break
            except Exception as e:                       # noqa: BLE001
                result = {"is_error": True, "errors": [f"{type(e).__name__}: {e}"]}
                print(f"    ! session raised {type(e).__name__}: "
                      f"{str(e)[:160]}")
                # A session that died may still have flushed most of its
                # batches. Retrying would throw those away and re-spend the
                # whole contract, so only retry when there is nothing to keep.
                partial, _ = read_predictions(root, real_of, clauses)
                if partial:
                    print(f"      keeping {len(partial)} judgments already "
                          f"written; not retrying")
                    break
                if attempt == 0:
                    print("      nothing written; retrying once")
                    await asyncio.sleep(30)
        since_rest += 1

        judged, why_not = read_predictions(root, real_of, clauses)
        if why_not:
            print(f"    ! {why_not}")
        cost = result.get("total_cost_usd") or 0.0

        # A short answer is not an answer. Go back for what is missing rather
        # than let unjudged provisions be scored as "not risky" by default.
        if len(judged) < len(clauses):
            judged, extra = await top_up(cid, root, real_of, clauses, judged,
                                         system_prompt, limits,
                                         result.get("session_id"))
            cost += extra.get("total_cost_usd") or 0.0
        spend += cost

        (LOGS / f"{cid}.json").write_text(json.dumps({
            "contract_id": cid, "model": MODEL, "effort": EFFORT,
            "n_provisions": len(clauses), **result,
            "rate_limits": {k: v.raw for k, v in limits.items()},
        }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        (RAW / f"{cid}.json").write_text(json.dumps({
            "contract_id": cid, "_id_map": real_of,
            "judgments": [dict(j, dataset_clause_id=k) for k, j in judged.items()],
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        n_ok = 0
        for c in clauses:
            j = judged.get(c["clause_id"])
            ok = j is not None
            n_ok += ok
            writer.writerow({
                "contract_id": cid, "citation": c["citation"],
                "clause_id": c["clause_id"], "clause_name": c["clause_name"],
                "label": c["label"], "taxonomy": c["taxonomy"],
                "gold": api.gold_of(c), "gold_subtype": api.gold_fine(c),
                "pred": api.pred_from_probs(api._f(j.get("prob_cat1")),
                                            api._f(j.get("prob_cat2"))) if ok else "",
                "prob_cat1": api._f(j.get("prob_cat1")) if ok else "",
                "prob_cat2": api._f(j.get("prob_cat2")) if ok else "",
                "reasoning_cat1": j.get("reasoning_cat1", "") if ok else "",
                "reasoning_cat2": j.get("reasoning_cat2", "") if ok else "",
                "ok": "1" if ok else "0"})
        fout.flush()

        missing = len(clauses) - n_ok
        u = result.get("usage") or {}
        print(f"    {n_ok}/{len(clauses)} judged"
              + (f"  ({missing} MISSING)" if missing else "")
              + f"  | {result.get('num_turns', '?')} turns, "
              f"{(result.get('duration_ms') or 0) / 1000:.0f}s, ${cost:.2f}"
              f"  [in {u.get('input_tokens', 0):,} "
              f"cache-read {u.get('cache_read_input_tokens', 0):,} "
              f"out {u.get('output_tokens', 0):,}]")
        if result.get("is_error"):
            print(f"    ! session error: {result.get('errors')}")

    fout.close()
    print(f"\n{len(todo)} session(s), ${spend:.2f} at API-equivalent rates")
    if todo:
        print(f"  -> {spend / len(todo):.2f} per contract, "
              f"projected {spend / len(todo) * len(groups):.2f} for all "
              f"{len(groups)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="run only N contracts (pilot)")
    ap.add_argument("--shuffle", action="store_true",
                    help="seeded random order instead of largest first")
    ap.add_argument("--seed", type=int, default=0)
    # Blind pacing only. The rate-limit-driven pause is NOT covered by this and
    # cannot be switched off: if the CLI reports the window exhausted, the run
    # waits for it whatever this says.
    ap.add_argument("--rest-every", type=int, default=20, metavar="N",
                    help=f"pause {REST_SECONDS // 3600}h every N contracts while "
                         f"the CLI reports no usable rate-limit reading; "
                         f"0 disables the pause")
    args = ap.parse_args()
    env()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()

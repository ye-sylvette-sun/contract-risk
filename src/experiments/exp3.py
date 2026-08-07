"""Experiment 3 — few-shot with judicial reasoning, judged by an agent session.

One Claude Agent SDK session per target contract. The session's working directory
holds the target contract as contract.md and the four example contracts as
example_<type>.md, so the agent can open any of them; its tools are Read and Grep
only, so it can read those files and nothing else.

The prompt carries four worked examples, one per risk type (1.1, 1.3, 2.1, 2.2).
Each is a PAIR from the same contract — the provision the court held risky, and a
clean provision beside it — plus the verbatim passage of the opinion where the
court construed the risky one. Pairing controls for document style: the model
cannot learn "this contract is the risky one", only what separates two provisions
of one instrument.

The four example contracts are excluded from evaluation.

Per provision the agent returns two independent probabilities, one per category;
a category is flagged at 0.5. Metrics are reported for category 1, category 2 and
overall risky-vs-not — no subtypes.

exp3_api.py is the ablation: same examples, same provisions, no contract.

Input : output/dataset.csv, output/contracts/*.md, prompts/exp3_judge.*
Output: output/exp3_preds.csv           per-provision predictions (resumable)
        output/exp3_raw/<id>.json       the agent's judgments, with gold injected
        output/exp3_logs/<id>.jsonl     the full SDK event stream
        output/exp3_examples.json       exactly which examples were used

Usage:
    conda run -n legal-llm python src/experiments/exp3.py
    conda run -n legal-llm python src/experiments/exp3.py --limit 2
    conda run -n legal-llm python src/experiments/exp3.py --metrics-only
"""
import argparse
import asyncio
import csv
import dataclasses
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib
from exp3_common import (FIELDS, FLAG, TYPES, done_ids, eval_groups,  # noqa: F401
                         example_block, gold_of, load_rows, metrics,
                         pick_examples, provision_block, rows_of)

# The Agent SDK must authenticate against the Claude Code SUBSCRIPTION, never an
# API key. This has to come AFTER the imports above, which load .env at import
# time and would otherwise put ANTHROPIC_API_KEY straight back.
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
assert not os.environ.get("ANTHROPIC_API_KEY"), \
    "ANTHROPIC_API_KEY is still set — the SDK would bill the key, not the plan"

import claude_agent_sdk as sdk

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

MODEL = "claude-opus-5"
EFFORT = "high"

WORK = lib.OUT / "_exp3_work"
RAW = lib.OUT / "exp3_raw"
LOGS = lib.OUT / "exp3_logs"
PREDS = lib.OUT / "exp3_preds.csv"


def build_prompt(clauses, block):
    return "\n".join([
        block,
        "\n\n" + "=" * 60 + "\n",
        "NOW JUDGE THE TARGET CONTRACT. Its full text is contract.md in your "
        "working directory. Read it, then judge every provision below.\n",
        f"There are {len(clauses)} target provisions, each with an id in "
        "square brackets. Return exactly one judgment per id.\n",
        provision_block(clauses),
        "\nFor every id above return prob_cat1 and prob_cat2 (each in [0,1], "
        "independent) with separate reasoning for each.",
    ])


# ------------------------------------------------------------------ agent ---
def stage(contract_id, target_file, examples):
    work = WORK / contract_id
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    src = lib.ROOT / target_file
    if not src.exists():
        return None
    shutil.copyfile(src, work / "contract.md")
    for e in examples:
        s = lib.ROOT / e["source"]
        if not s.exists():
            return None
        shutil.copyfile(s, work / e["file"])
    return work


def to_jsonable(o):
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return {"_type": type(o).__name__,
                **{f.name: to_jsonable(getattr(o, f.name))
                   for f in dataclasses.fields(o)}}
    if isinstance(o, dict):
        return {str(k): to_jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [to_jsonable(x) for x in o]
    if isinstance(o, (str, int, float, bool)) or o is None:
        return o
    return repr(o)


async def judge(contract_id, clauses, block, examples, sem, retries=1):
    work = stage(contract_id, clauses[0]["contract_file"], examples)
    if work is None:
        print(f"  ! {contract_id}: a contract file is missing", file=sys.stderr)
        return None
    opts = sdk.ClaudeAgentOptions(
        model=MODEL, effort=EFFORT, cwd=str(work), max_turns=24,
        allowed_tools=["Read", "Grep"], disallowed_tools=["Bash", "Write", "Edit"],
        permission_mode="bypassPermissions", setting_sources=[],
        system_prompt=(lib.PROMPTS / "exp3_judge.md").read_text(encoding="utf-8"),
        output_format={"type": "json_schema",
                       "schema": lib.schema("exp3_judge")},
    )
    prompt = build_prompt(clauses, block)
    async with sem:
        for attempt in range(retries + 1):
            try:
                out, msgs = None, []
                async for m in sdk.query(prompt=prompt, options=opts):
                    msgs.append(m)
                    if isinstance(m, sdk.ResultMessage):
                        out = m.structured_output
                (LOGS / f"{contract_id}.jsonl").write_text(
                    "\n".join(json.dumps(to_jsonable(m), ensure_ascii=False)
                              for m in msgs), encoding="utf-8")
                if out and isinstance(out.get("judgments"), list):
                    return out
            except Exception as e:                                  # noqa: BLE001
                if attempt == retries:
                    print(f"  ! {contract_id} failed: {e}", file=sys.stderr)
    return None


# -------------------------------------------------------------------- run ---
async def run(limit, concurrency):
    rows = load_rows()
    examples = pick_examples(rows)
    block = example_block(examples, with_files=True)
    lib.write_json(lib.OUT / "exp3_examples.json", examples)
    groups = eval_groups(rows, examples)

    n_eval = sum(len(v) for v in groups.values())
    n_pos = sum(1 for v in groups.values() for r in v if r["label"] == "POSITIVE")
    print(f"examples ({len(examples)} pairs, contracts held out of evaluation):")
    for e in examples:
        print(f"  {e['code']}  {e['citation']:20} {e['contract_id'][-34:]:36} "
              f"risky={len(e['risky']['text'])}ch clean={len(e['clean']['text'])}ch "
              f"opinion={len(e['risky']['opinion'])}ch")
    print(f"\neval set: {n_eval} provisions ({n_pos} risky) in {len(groups)} "
          f"contracts | model {MODEL}, effort {EFFORT}")

    done = done_ids(PREDS)
    todo = [(cid, cls) for cid, cls in groups.items() if cid not in done]
    if limit:
        todo = todo[:limit]
    print(f"{len(done)} contracts already done, judging {len(todo)} "
          f"(concurrency {concurrency})")
    if not todo:
        metrics(PREDS, "experiment 3 — agent session, contract visible")
        return

    for d in (RAW, LOGS, WORK):
        d.mkdir(parents=True, exist_ok=True)
    new = not PREDS.exists()
    fout = PREDS.open("a", newline="", encoding="utf-8")
    writer = csv.DictWriter(fout, fieldnames=FIELDS)
    if new:
        writer.writeheader()

    def write_group(contract_id, clauses, out):
        judgments = (out or {}).get("judgments", [])
        if out:
            gold_map = {str(c["clause_id"]): c for c in clauses}
            for j in judgments:
                c = gold_map.get(str(j.get("clause_id")))
                if c:
                    j["gold"] = gold_of(c)
                    j["clause_name"] = c["clause_name"]
            (RAW / f"{contract_id}.json").write_text(
                json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
        out_rows = rows_of(contract_id, clauses, judgments)
        writer.writerows(out_rows)
        fout.flush()
        return sum(1 for r in out_rows if r["ok"] == "1")

    sem = asyncio.Semaphore(concurrency)
    t0, n = time.time(), 0
    for i in range(0, len(todo), concurrency):
        wave = todo[i:i + concurrency]
        results = await asyncio.gather(
            *[judge(cid, cls, block, examples, sem) for cid, cls in wave])
        for (cid, cls), out in zip(wave, results):
            got = write_group(cid, cls, out)
            n += 1
            print(f"  [{n}/{len(todo)}] {cid[-40:]}: {got}/{len(cls)} judged"
                  f"{'' if out else '   FAILED'}")
        el = time.time() - t0
        print(f"    elapsed {el:.0f}s, eta {el / n * (len(todo) - n):.0f}s")

    fout.close()
    shutil.rmtree(WORK, ignore_errors=True)
    print(f"\njudging finished in {time.time() - t0:.0f}s")
    metrics(PREDS, "experiment 3 — agent session, contract visible")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="max contracts (0 = all)")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--metrics-only", action="store_true")
    args = ap.parse_args()
    if args.metrics_only:
        metrics(PREDS, "experiment 3 — agent session, contract visible")
        return
    asyncio.run(run(args.limit, args.concurrency))


if __name__ == "__main__":
    main()

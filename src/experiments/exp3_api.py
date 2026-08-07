"""Experiment 3, ablated — the same few-shot judgment as one stateless API call.

exp3.py gives a Claude Agent SDK session the whole contract on disk and lets it
read around each provision. This variant removes exactly that. One plain
Messages API call per contract carries the four worked example pairs (each with
the court's own words about the risky provision) and the list of target
provisions — and nothing else. No contract text, no tools, no files, no turns.

Everything else is held fixed and shared through exp3_common: the same example
selection, the same held-out contracts, the same provisions in the same batches,
the same two probabilities, the same 0.5 flag, the same metrics. The difference
between the two result sets is therefore attributable to the contract being in
the input or not, which is the point of running it.

Category 2 is the interesting half. It is defined by a provision's relationship
to the rest of the instrument, so an input without the instrument should hurt it
more than category 1 — if it does not, the agent was not really using the
contract.

Input : output/dataset.csv, prompts/exp3_api_judge.*
Output: output/exp3_api_preds.csv        per-provision predictions (resumable)
        output/exp3_api_raw/<id>.json    the model's judgments, with gold injected
        output/llm_logs/exp3_api_judge/  the full request and response per call
        output/exp3_examples.json        exactly which examples were used

Usage:
    conda run -n legal-llm python src/experiments/exp3_api.py
    conda run -n legal-llm python src/experiments/exp3_api.py --limit 1
    conda run -n legal-llm python src/experiments/exp3_api.py --metrics-only
"""
import argparse
import csv
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib
from exp3_common import (FIELDS, done_ids, eval_groups, example_block, gold_of,
                         load_rows, metrics, pick_examples, provision_block,
                         rows_of)

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

EFFORT = "high"                 # matched to exp3's agent sessions
PROMPT = "exp3_api_judge"
TITLE = "experiment 3 (API) — single call, no contract"

RAW = lib.OUT / "exp3_api_raw"
PREDS = lib.OUT / "exp3_api_preds.csv"


def judge(contract_id, clauses, block, retries=1):
    """One stateless call for one contract's provisions. None if it never lands.

    The examples go in the DOCUMENT block, which lib.ask marks for caching and
    which is byte-identical across every call in a run — so only the first
    contract pays for them.
    """
    for attempt in range(retries + 1):
        try:
            out = lib.ask(PROMPT, contract_id, effort=EFFORT,
                          examples=block, n_clauses=len(clauses),
                          provisions=provision_block(clauses))
            if out and isinstance(out.get("judgments"), list):
                return out
        except Exception as e:                                      # noqa: BLE001
            if attempt == retries:
                print(f"  ! {contract_id} failed: {e}", file=sys.stderr)
    return None


def run(limit, concurrency):
    rows = load_rows()
    examples = pick_examples(rows)
    # with_files=False: there is no working directory to name a file in.
    block = example_block(examples, with_files=False)
    groups = eval_groups(rows, examples)

    n_eval = sum(len(v) for v in groups.values())
    n_pos = sum(1 for v in groups.values() for r in v if r["label"] == "POSITIVE")
    print(f"examples ({len(examples)} pairs, contracts held out of evaluation):")
    for e in examples:
        print(f"  {e['code']}  {e['citation']:20} {e['contract_id'][-34:]:36} "
              f"risky={len(e['risky']['text'])}ch clean={len(e['clean']['text'])}ch "
              f"opinion={len(e['risky']['opinion'])}ch")
    print(f"\neval set: {n_eval} provisions ({n_pos} risky) in {len(groups)} "
          f"contracts | model {lib.MODEL}, effort {EFFORT}, no contract in input")

    done = done_ids(PREDS)
    todo = [(cid, cls) for cid, cls in groups.items() if cid not in done]
    if limit:
        todo = todo[:limit]
    print(f"{len(done)} contracts already done, judging {len(todo)} "
          f"(concurrency {concurrency})")
    if not todo:
        metrics(PREDS, TITLE)
        return

    RAW.mkdir(parents=True, exist_ok=True)
    new = not PREDS.exists()
    fout = PREDS.open("a", newline="", encoding="utf-8")
    writer = csv.DictWriter(fout, fieldnames=FIELDS)
    if new:
        writer.writeheader()

    t0, n = time.time(), 0
    # The first call is sent alone so the example block lands in the cache before
    # the rest go out together and all read it.
    waves = [todo[:1], todo[1:]] if len(todo) > 1 else [todo]
    for wave in waves:
        if not wave:
            continue
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            results = list(pool.map(
                lambda item: judge(item[0], item[1], block), wave))
        for (cid, cls), out in zip(wave, results):
            judgments = (out or {}).get("judgments", [])
            if out:
                gold_map = {str(c["clause_id"]): c for c in cls}
                for j in judgments:
                    c = gold_map.get(str(j.get("clause_id")))
                    if c:
                        j["gold"] = gold_of(c)
                        j["clause_name"] = c["clause_name"]
                (RAW / f"{cid}.json").write_text(
                    json.dumps(out, indent=2, ensure_ascii=False),
                    encoding="utf-8")
            out_rows = rows_of(cid, cls, judgments)
            writer.writerows(out_rows)
            fout.flush()
            got = sum(1 for r in out_rows if r["ok"] == "1")
            n += 1
            print(f"  [{n}/{len(todo)}] {cid[-40:]}: {got}/{len(cls)} judged"
                  f"{'' if out else '   FAILED'}")
        print(f"    elapsed {time.time() - t0:.0f}s")

    fout.close()
    print(f"\njudging finished in {time.time() - t0:.0f}s")
    metrics(PREDS, TITLE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="max contracts (0 = all)")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--metrics-only", action="store_true")
    args = ap.parse_args()
    if args.metrics_only:
        metrics(PREDS, TITLE)
        return
    run(args.limit, args.concurrency)


if __name__ == "__main__":
    main()

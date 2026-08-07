# Experiment 3 — few-shot with judicial reasoning

Can a model flag a contract provision a court would hold defective, given four
worked examples of courts doing exactly that?

The experiment ships in **two versions that differ in one thing**: whether the
model can see the contract the provision came from.

| | version 1 — agent | version 2 — API |
|---|---|---|
| script | `src/experiments/exp3.py` | `src/experiments/exp3_api.py` |
| surface | Claude Agent SDK session | one stateless Messages API call |
| contract text | **yes** — `contract.md` in the working directory | **no** |
| tools | `Read`, `Grep` | none |
| turns | up to 24 | 1 |
| auth | Claude Code subscription | `ANTHROPIC_API_KEY` |
| predictions | `output/exp3_preds.csv` | `output/exp3_api_preds.csv` |

Everything else is held fixed and shared through `src/experiments/exp3_common.py`
— the same examples, the same held-out contracts, the same provisions in the same
batches, the same threshold, the same metrics. The difference between the two
result sets is therefore attributable to the contract being in the input or not.

Both use `claude-opus-5` at **high** reasoning effort, and both judge **one
contract per call/session**: every provision of a contract is judged together, so
the model sees them as a set rather than in isolation.

---

## Shared setup

### The four example pairs

One risk type each — 1.1, 1.3, 2.1, 2.2 (1.2 and 2.3 have no positives in the
dataset). Each is a **pair from the same contract**:

* the provision a court held to carry the risk,
* the verbatim passage of the opinion where the court construed it,
* and a clean provision from the *same* instrument.

Pairing controls for document style. Without it the model can learn "this
contract is the risky one"; with it, the only thing that separates the two
examples is the defect.

Selection is mechanical, not hand-picked (`pick_examples`). Among the contracts
carrying a positive of a type, take the one that costs the fewest positives to
withhold — positives are the scarce class and every example contract must leave
the evaluation set. Ties go to the smaller contract, then to id. Within it, take
the first positive in document order and pair it with the negative whose text
length is closest, so length cannot be what tells them apart.

The four example contracts are **excluded from evaluation**, leaving **117
provisions (6 risky) across 3 contracts**. The chosen examples are written to
`output/exp3_examples.json`.

### Gold, prediction, metrics

Gold comes from `dataset.csv` and collapses the subtypes:

```
label != POSITIVE           -> not_risky
taxonomy starts with "1"    -> risky_cat1     intrinsic textual defect
otherwise                   -> risky_cat2     defect relative to the instrument
```

The model returns **two independent probabilities per provision**, not one
distribution — a provision may carry neither risk, one, or both. A category is
flagged at **0.5**. For the single-label view, both below 0.5 is `not_risky`,
otherwise the likelier category wins.

Reported: precision / recall / F1 for **category 1**, **category 2**, and overall
**risky vs not**. No subtypes.

---

## Version 1 — agent session (`exp3.py`)

**Input.** Per target contract, a working directory is staged with:

```
contract.md        the target instrument
example_1_1.md     the four example contracts, in full
example_1_3.md
example_2_1.md
example_2_2.md
```

The session is given `prompts/exp3_judge.md` as its system prompt and a user turn
containing the four worked example pairs (each naming its file on disk) followed
by every target provision, each with an id in square brackets. Tools are
restricted to `Read` and `Grep`; `Bash`, `Write` and `Edit` are denied, and
`setting_sources=[]` keeps any local Claude Code configuration out of the run.

The prompt tells it to study the examples first, then read `contract.md`, and to
raise `prob_cat2` only after actually checking another provision — and to name
it.

**Output.** `output_format` is a JSON schema
(`prompts/exp3_judge.schema.json`), read off `ResultMessage.structured_output`:

```jsonc
{ "judgments": [ {
    "clause_id":      "neg7",       // exactly as given in the prompt
    "prob_cat1":      0.32,
    "prob_cat2":      0.18,
    "reasoning_cat1": "...",        // the wording alone
    "reasoning_cat2": "..."         // fit with the rest of the instrument
} ] }
```

The full SDK event stream is written to `output/exp3_logs/<contract_id>.jsonl`
and the judgments, with gold injected, to `output/exp3_raw/<contract_id>.json`.

---

## Version 2 — single API call (`exp3_api.py`)

The ablation. Same examples, same provisions, same schema — **no contract**.

**Input.** One `lib.ask()` call per contract, built from
`prompts/exp3_api_judge.md`:

* `SYSTEM` — the analyst role, the risk definitions, calibration. It states
  plainly that there is no contract and no tools, and tells the model to judge
  category 2 from what the provision itself exposes: cross-references, provisos,
  `notwithstanding` language, terms used as defined but not defined, priority and
  merger clauses — and from the other provisions in the same list, which it is
  told come from the same contract. Not seeing the contract is a reason to stay
  low, not a reason to guess.
* `DOCUMENT` — the four worked pairs, without the `[file: …]` references since
  there is no working directory. This block is byte-identical across every call
  in a run and is `cache_control`-marked, so only the first contract pays for it.
* `TASK` — the target provisions, in the same format as version 1.

**Output.** Identical shape to version 1, via `output_config.format` with
`prompts/exp3_api_judge.schema.json`. Note that this schema omits the numeric
`minimum`/`maximum` that the agent variant carries — the Messages API structured
output dialect rejects numeric range keywords — so the range is stated in the
description and the reader clamps to [0,1].

Raw judgments go to `output/exp3_api_raw/<contract_id>.json`; the complete
request and response of every call to `output/llm_logs/exp3_api_judge/`.

---

## Results

117 provisions, 6 gold-risky, 3 contracts. Threshold 0.5.

| channel | agent (contract visible) | API (no contract) |
|---|---|---|
| category 1 | P 0.50 / R 0.33 / F1 0.40 | P 0.50 / R 0.67 / F1 0.57 |
| category 2 | P 0.50 / R 0.67 / F1 0.57 | P 0.50 / R 0.67 / F1 0.57 |
| **risky vs not** | **P 1.00 / R 1.00** | **P 0.75 / R 1.00** |

Both versions found **all 6** risky provisions. What removing the contract cost
was precision and margin:

* **False alarms.** Agent 0, API 2 — and both API false alarms land at exactly
  0.50, on the category it could not check.
* **Separation collapses.** Agent: lowest positive 0.50, highest negative 0.45,
  a margin of **+0.05**. API: 0.50 against 0.50, a margin of **0.00**. The binary
  channel is then decided by ties, which is where the drop to P 0.75 comes from.
* **Category 1 recall goes up** without the contract, 0.33 → 0.67. With no
  instrument to consult the model reads harder into the wording itself: across
  the 6 positives `prob_cat1` rose on 4 and `prob_cat2` fell on 5.

Cost: the API run is **$1.26** and 399 s for all 117 provisions (the example
block cached after the first call — 16,384 tokens written once, read twice). The
agent run bills the subscription.

### Figures

`src/experiments/plot_thresholds.py` sweeps the threshold over [0,1] and plots,
per channel, precision and recall (top row) and the share of provisions flagged
(bottom row), with a dotted line at the 0.5 operating point.

```
output/figures/exp3_thresholds.png       version 1
output/figures/exp3_api_thresholds.png   version 2
```

The sweep is the point. A single operating point says precision and recall are
both 1.00 on the agent's binary channel; the curve shows how much of that is
headroom and how much is luck, because it shows where the number breaks.

---

## Reading these numbers honestly

* **6 positives.** The difference between P 1.00 and P 0.75 is two provisions.
  Nothing here is significant; it is a working measurement on a dataset still
  being built.
* **The ablation is softer than "no contract" sounds.** The target provisions
  themselves already reconstitute 55–74 % of each contract's lines (74 % / 55 % /
  56 % for the three). What version 2 actually removes is document order,
  adjacency, and the untabulated remainder — not all knowledge of the instrument.
  The prompt makes this explicit rather than pretending otherwise.
* **Two definitions of per-category P/R are in play.** The metrics table assigns
  each provision *one* label, so a category-1 provision predicted category 2
  counts against both. The threshold sweep treats each channel independently,
  ranking by that channel's own probability. Both are reported; they are not the
  same quantity, and the sweep's numbers are the more forgiving.
* **All the errors are category confusion, not false alarm** in version 1 — it
  never flagged a clean provision, it just sometimes picked the wrong reason.

---

## Running

```sh
python src/experiments/exp3.py                    # version 1, all contracts
python src/experiments/exp3.py --limit 1          # one contract
python src/experiments/exp3_api.py                # version 2
python src/experiments/exp3_api.py --metrics-only # re-report, no calls
python src/experiments/plot_thresholds.py         # both figures
python src/experiments/plot_thresholds.py --run api
```

Both are resumable: predictions are appended per contract and a contract with a
successful row is not re-judged. `--concurrency` controls how many run at once.

`exp3.py` **removes `ANTHROPIC_API_KEY` from the environment at import**, after
importing `lib` (which loads `.env`), so the Agent SDK authenticates against the
Claude Code subscription rather than billing the key. A module-level assert makes
the failure loud rather than expensive. This is also why the two variants share
`exp3_common.py` instead of one importing the other.

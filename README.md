# Contract-clause risk dataset

A labelled dataset of **risky vs non-risky contract clauses**, built from federal
court opinions and the contracts filed with them, plus an experiment that asks a
model to predict the labels.

```
6,835 rows  |  144 positive / 6,691 negative  (2.1% positive)
39 cases    |  67 contracts
```

- **[docs/DATASET.md](docs/DATASET.md)** — what a label means, how the dataset is
  built, the columns, the known limits.
- **[docs/EXPERIMENTS.md](docs/EXPERIMENTS.md)** — the prediction experiment: two
  runs, what the model is given, how it is scored.
- **[docs/REPORT.md](docs/REPORT.md)** — the findings: what the agentic harness
  bought, how robust it is, and what the session logs do and do not explain.

This README is the repo tour and how to run it.

## What "risky" means

> **Positive** — the opinion shows the two sides disputed the clause and the court
> discussed it, *whatever the court decided*.

The litigation is itself the cost. A clause that had to be argued over in front of
a federal judge has already failed at its job, even where the judge read it the
way its drafter intended — so a clause the court **upheld**, or held **clear**,
counts as much as one construed against its drafter.

> **Negative** — any other clause of any contract filed in the same case.

An unlitigated clause is **lower risk, not sound**: it may be well drafted, or it
may carry a defect nobody had occasion to fight over. Precision against these
labels is a lower bound.

The risk *type* is never a model's opinion either — it comes from the Westlaw Key
Number the case was filed under.

**The classes are not matched on clause length, deliberately.** Positives run
longer (median 607 characters against 372; P(positive longer) = **0.655**). The
clauses parties take to court are the long, qualified, heavily conditioned ones.
Length is left in rather than sampled away — the dataset's job is to record what
the corpus is, not to look balanced. **If you benchmark on this, report a
length-only baseline alongside your model**, and report per-contract as well as
aggregate numbers.

## What guarantees the text

The model never writes clause text. For each clause it returns a line range plus
a verbatim anchor at each end — the first and last eight words, copied exactly as
the scan shows them, OCR damage and all. `lib.locate()` matches those anchors
against the contract file, snaps the boundary to where they actually are, and
slices the text out of the file itself. An anchor that matches nothing near the
claimed window means the model pointed at the wrong place, and the clause is
rejected with its reason recorded.

`build_dataset.py` then re-cuts **every** row from disk at its recorded character
span and refuses to write unless it reproduces `clause_text` exactly.

## Layout

```
data/                    Westlaw headnotes, opinion text, the docket linking sheet
contract_risk/           the Contract-Risk repo's new_approach/ — OCR'd contract text

prompts/                 <name>.md (SYSTEM/DOCUMENT/INSTRUCTIONS/TASK) + <name>.schema.json
  layout                 step 0b — is the scan an interleaved two-column page?
  extract                step 1 — locate the clauses the parties disputed
  inventory              step 2 — locate every clause of one contract
  exp3                   the experiment's judging criteria

src/lib.py               paths, taxonomy, ask(), locate(), normalise()
src/step0_corpus.py      0.  link cases, register documents        (no LLM)
src/step0b_layout.py     0b. reject two-column scans              (Sonnet, per contract)
src/step1_extract.py     1.  which clauses were disputed          (LLM, per case)
src/step2_inventory.py   2.  every clause of every contract       (LLM, per contract)
src/build_dataset.py         assemble + validate -> dataset.csv   (no LLM)
src/replay_anchors.py        re-score the locator against stored logs (no LLM, no cost)

src/experiments/exp3_llm_api.py          one API call per contract
src/experiments/exp3_agent.py            one agent session per contract
src/experiments/plot_exp3_thresholds.py  --run {llm_api,agent}

output/cases.json        cases in scope, with keys, headnotes and codes
output/contracts.json    the document registry
output/contracts/<cid>.md   document text, OCR furniture stripped
output/layout.json       step 0b — the two-column verdict, with the model's evidence
output/clauses.json      step 1 — positives, and what was rejected
output/inventory.json    step 2 — every clause of every contract, and the flags
output/llm_logs/<step>/  full prompt, response and usage for every call
output/dataset.csv       the dataset
output/exp3_<run>_*      the experiment's predictions, raw answers and figures
```

`data/` and `contract_risk/` are gitignored — they are large and provided
separately. No path escapes the repo, so link them in rather than reaching out of
the tree:

```sh
ln -s /path/to/legal-llm-data              data           # or a Windows junction
ln -s /path/to/Contract-Risk/new_approach  contract_risk
```

The contract text is not OCR'd here. Contract-Risk downloaded the docket filings,
OCR'd them (`ocrmypdf --force-ocr`), had a model say which file holds which named
agreement, and sliced each file to that agreement's own lines — a **verbatim**
line-range cut, independently checked here: all 205 re-sliceable extractions are
byte-identical to their source. Step 0 registers only the entries their
`contract_check.csv` marks `usable`.

## Running

```sh
pip install -r requirements.txt
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

python src/step0_corpus.py       # 0.  no LLM
python src/step0b_layout.py      # 0b. one cheap Sonnet call per contract
python src/step1_extract.py      # 1.  one call per case
python src/step2_inventory.py    # 2.  one call per contract step 1 was shown
python src/build_dataset.py      #     no LLM — validates, then writes
```

`--case "44 F.Supp.3d 736"` restricts any step to one citation, which is how to
try the pipeline end to end before paying for a full run. `step0b_layout.py` and
`step2_inventory.py` also take `--contract <contract_id>`.

**Steps 0b, 1 and 2 are resumable.** Each writes its artifact after every call and
re-runs only what is missing. To *redo* work already done — after a prompt change,
say — delete that case or contract id from `clauses.json` / `inventory.json`
first, or the step will skip it and you will conclude the change had no effect.
Step 0b takes `--force` instead.

Step 0 and `build_dataset.py` make no model calls, so the dataset is reproducible
from the stored artifacts without an API key.

### Re-scoring the locator for free

A log holds both the numbered document that was sent and the model's answer, so
the locator can be re-scored offline at no API cost:

```sh
python src/replay_anchors.py --logs output/llm_logs
```

It **replays** the anchors the model actually returned, which is how `ANCHOR_MATCH`
and `SLACK` are tuned against real answers. It reports what fraction of anchors
match, how often snapping moves a boundary and by how much, and how often the
extracted text differs from the raw source window. A difference is not
automatically a fault — the anchors cut *inside* the first and last line, so a
heading's markdown is legitimately left out. What it catches is a span that lost
real words.

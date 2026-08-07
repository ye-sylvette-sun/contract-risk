# Contract-clause risk dataset

A labelled dataset of **risky vs non-risky contract provisions**, built from
federal opinions and the litigation documents filed with them — plus experiments
that test whether a model can reproduce the labels.

"Risky" is never a model's opinion. A provision is positive because a federal
court construed it as carrying a specific construction defect; the risk type
comes from the Westlaw Key Number the case was filed under, and every positive
points at the passage of the opinion where the court did the construing.

The two halves of the repo are built on deliberately different footings:

* **The dataset pipeline** uses only single stateless Claude Messages API calls —
  no tools, no agent loop, no multi-turn session. It must not be built by the
  same kind of system it is used to evaluate.
* **The experiments** are agentic on purpose. That is the object of study.

**Docs:** [docs/PIPELINE.md](docs/PIPELINE.md) — how the dataset is built ·
[docs/EXPERIMENT.md](docs/EXPERIMENT.md) — the two experiment versions.

## Layout

```
data/                    Westlaw headnotes, opinion text, the docket linking sheet
bloomberg/               contract bundles as downloaded (PDF)
bloomberg_datalab/       the same bundles after Datalab Marker OCR (markdown)

prompts/                 one <name>.md (SYSTEM/DOCUMENT/TASK) + <name>.schema.json per call
  triage, survey, extract          the three pipeline steps
  exp3_judge, exp3_api_judge       the two experiment versions

src/lib.py               paths, taxonomy, the API call, numbered(), verify()
src/link_cases.py        0. join headnotes x opinions x downloadable contracts
src/step_a_triage.py     A. is this case worth acquiring?                  (LLM)
src/step_b_survey.py     B. instrument boundaries + provision inventory    (LLM)
src/build_contracts.py      stitch, cut and deduplicate -> output/contracts/
src/step_c_extract.py    C. which provision did the court construe?        (LLM)
src/build_dataset.py        assemble + validate -> output/dataset.csv

src/experiments/exp3.py           version 1 — Claude Agent SDK, contract visible
src/experiments/exp3_api.py       version 2 — one API call, no contract
src/experiments/exp3_common.py    what both must share for the comparison to hold
src/experiments/plot_thresholds.py

output/dataset.csv       the dataset (242 rows)
output/contracts/*.md    the instruments the rows are cut from
output/opinions/*.txt    opinion text, keyed by case id
output/*.json            each step's artifact (cases, survey, contracts, clauses)
output/llm_logs/         every pipeline call: system, document, task, response, usage
output/exp3*_preds.csv   per-provision predictions
output/exp3_logs/        full agent event streams
output/figures/          the threshold-sweep figures
```

`data/`, `bloomberg/` and `bloomberg_datalab/` are gitignored — they are 1.7 GB
and exceed GitHub's limits, so they are provided separately. Everything the code
reads lives inside the repo; no path escapes it.

## What's in the dataset

**242 rows — 14 positive, 228 negative**, drawn from 7 instruments across 10
cases run end to end. Positives by risk type: 1.1 = 2, 1.3 = 4, 2.1 = 3, 2.2 = 5
(types 1.2 and 2.3 have no downloaded case that yielded one).

Negatives are the *other* provisions of the same instrument the positives came
from, so both classes are the same document in the same OCR condition — a
classifier cannot win by recognising document style.

Each row carries the repaired text, the raw source window, and what was repaired,
so every edit is auditable. Repairs touch 0.98 % of words.

## Running

```sh
pip install -r requirements.txt
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

python src/link_cases.py         # 0.  no LLM
python src/step_a_triage.py      # A.  optional — steers acquisition only
#    ... download the shortlisted bundles, OCR them with Datalab ...
python src/step_b_survey.py      # B.
python src/build_contracts.py    #     no LLM
python src/step_c_extract.py     # C.
python src/build_dataset.py      #     no LLM — validates, then writes

python src/experiments/exp3.py             # version 1
python src/experiments/exp3_api.py         # version 2
python src/experiments/plot_thresholds.py
```

Steps A, B and C are resumable — each writes its artifact after every call and
re-runs only what is missing. `--case "44 F.Supp.3d 736"` restricts a step to one
citation, which is how to try the pipeline end to end before paying for a full
run. `build_contracts.py` and `build_dataset.py` make no model calls, so the
dataset is reproducible from the stored artifacts without an API key.

## What guarantees the text

Every step that must produce source text returns a **line window** into a named
file *plus* that text with OCR damage repaired. `verify()` in
[src/lib.py](src/lib.py) aligns the two word for word: only substitutions within
Levenshtein distance 2 pass, no word may be added or removed, and no number may
change. A hallucinated clause has no alignment; `shall not` → `shall` is a
deletion and is rejected. The line window is provenance; the emitted text is the
boundary. Nothing becomes dataset text by another route.

`build_dataset.py` re-runs that check from disk on every row before writing, and
refuses to write if one fails.

# Contract-clause risk dataset

A labelled dataset of **risky vs non-risky contract clauses**, built from federal
court opinions and the contracts filed with them, plus an experiment that asks a
model to predict the labels.

```
11,798 rows  |  201 positive / 11,597 negative  (1.7% positive)
62 cases     |  103 contracts                   |  12.4 MB
```

> **The experiment is agentic.** `risk_detect_agent.py` — one sandboxed Claude Code
> session per contract — is the experiment that is run and reported.
> `risk_detect_llm_api.py`, the one-shot API arm, has been **retired**: it is kept as
> the reference implementation of the scoring contract the agent arm reuses, but
> it is not run and its numbers are not maintained. The old two-arm comparison
> is on the `legacy_spellbook_9.1` branch.

- **[docs/DATASET.md](docs/DATASET.md)** — what a label means, how the dataset is
  built, the columns, the known limits.
- **[docs/EXPERIMENTS.md](docs/EXPERIMENTS.md)** — the prediction experiment:
  what the model is given, how it is scored.
- **[docs/REPRODUCIBILITY.md](docs/REPRODUCIBILITY.md)** — what the agent run is
  isolated from, and how that is checked.
- **[docs/REPORT.md](docs/REPORT.md)** — the results: what the agent run found,
  and what is not established.

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

The risk *type* comes from the Westlaw Key Number the case was filed under, not
from a model's opinion — except where a case was filed under several, when a
model says which of *those* the dispute turned on and `taxonomy_provenance`
records that it did. The binary risky/not label never depends on this.

**The classes are not matched on clause length, and currently do not need to
be.** Positives and negatives run to almost the same length (median 329
characters against 331), and clause length alone separates them at
within-contract ROC-AUC **0.523** [0.501, 0.546] — indistinguishable from
chance.

This was **not** true of the previous build, where positives ran to a median 607
against 372 and length alone reached **0.683**. The gap closed because the
rebuilt step 1 cuts tighter spans, not because the task changed. So: **if you
benchmark on this, re-measure the length-only baseline for whatever build you
have** rather than trusting this number, and report per-contract as well as
aggregate figures.

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
  layout                    step 0b — is the scan an interleaved two-column page?
  extract                   step 1 — locate the clauses the parties disputed
  inventory                 step 2 — locate every clause of one contract
  risk_detect               the risk-detection experiment's judging criteria
  issue_alignment_check     did an issue match the dispute the court had?

src/lib.py               paths, taxonomy, ask(), locate(), normalise()
src/step0_corpus.py      0.  link cases, register documents        (no LLM)
src/step0b_layout.py     0b. reject two-column scans        (cheap model, per contract)
src/step1_extract.py     1.  which clauses were disputed          (LLM, per case)
src/step2_inventory.py   2.  every clause of every contract       (LLM, per contract)
src/build_dataset.py         assemble + validate -> dataset.csv   (no LLM)
src/replay_anchors.py        re-score the locator against stored logs (no LLM, no cost)

src/experiments/
  risk_detect_agent.py            THE risk-detection run: one agent session per contract, in a container
  risk_detect_llm_api.py          RETIRED one-shot arm; kept as the scoring contract the agent imports
  issue_alignment_check.py        was a named issue the defect the court actually construed?
  plot_risk_detect_thresholds.py  the threshold figure           --run agent
  compare_risk_detect.py          ROC, precision, recall, flag rate (needs two runs)
  predictions.py                  reading the judgment files the agent writes
  isolation.py                    what a session may see: tools, path hook, env
  test_isolation.py               the path hook's cases (no cost, no API)
  preflight.py                    one session, then audit it for leakage
  manifest.py                     what the machine was, per run
  lockgen.py                      regenerate the hash-pinned docker/requirements.lock.txt

docker/Dockerfile                        the judging sandbox, base pinned by digest
docker/judge_one.py                      the only code that runs inside it

output/cases.json        cases in scope, with keys, headnotes and codes
output/contracts.json    the document registry
output/contracts/<cid>.md   document text, OCR furniture stripped
output/layout.json       step 0b — the two-column verdict, with the model's evidence
output/clauses.json      step 1 — positives, and what was rejected
output/inventory.json    step 2 — every clause of every contract, and the flags
output/llm_logs/<step>/  full prompt, response and usage for every call
output/dataset.csv       the dataset
output/risk_detect_<run>_*   risk detection: predictions, raw answers, figures
output/issue_alignment_check.*  one row per issue judged against the court's words
output/llm_logs/risk_detect_agent/run_manifest_<stamp>.json
                         SDK, CLI, interpreter, platform, git commit, the
                         environment sweep, the options, and the input hashes
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
cat > .env <<'KEYS'
OPENAI_API_KEY=sk-proj-...            # the dataset build (steps 0b, 1, 2)
CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...   # risk detection (`claude setup-token`)
KEYS
```

**Two providers, on purpose.** The dataset build runs on OpenAI
(`gpt-5.6-sol`, and `gpt-5.6-terra` for the cheap layout screen); the risk-detection experiment
runs on Claude, because it *is* the Claude Code CLI. `lib.provider_of()` picks
the transport from the model name, so a step that names its model has already
chosen its API.

**Then authenticate the subscription, by hand, once.** The dataset steps bill the
OpenAI API key; the experiment bills the Claude Code **subscription** — and it
runs each session in a container with no `~/.claude` to log in from, so the
credential has to be passed in. No Anthropic API key is needed, and none reaches
the container: `docker run` passes only `-e CLAUDE_CODE_OAUTH_TOKEN`, and
`isolation.env()` strips every `ANTHROPIC_*` variable inside the container before
the SDK starts.

```sh
claude setup-token          # opens a browser; prints a long-lived (1-year) token
```

Run that in a **real terminal**. It cannot be scripted: the flow is a full-screen
prompt that needs a TTY, and it exits the moment its output is piped to a file or
another process. Then put what it printed in `.env`, next to the API key — the
run loads it from there:

```sh
CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...
```

An exported `CLAUDE_CODE_OAUTH_TOKEN` also works and wins over `.env`. Only the
token is handed to the container (`docker run -e`); the API key is not.

If that variable is unset, `risk_detect_agent.py` falls back to bind-mounting
`~/.claude/.credentials.json` read-only, as a single file — never the directory,
which would carry `settings.json`, `CLAUDE.md` and the memory store in with it.
That works, but a read-only mount cannot refresh an access token that expires
part-way through a multi-hour run, which is why the token is preferred.

Now the dataset:

```sh
python src/step0_corpus.py       # 0.  no LLM
python src/step0b_layout.py      # 0b. one cheap call per contract
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

Then the experiment:

```sh
docker build -f docker/Dockerfile -t contract-risk-judge:0.2.139 .
python src/experiments/test_isolation.py      # the path hook's cases, no cost
python src/experiments/preflight.py --container   # one real session, then audit it

python src/experiments/risk_detect_agent.py --shuffle --parallel 8
python src/experiments/plot_risk_detect_thresholds.py --run agent
```

`--parallel` sets how many containers run at once; they are independent sessions
against one subscription, so the only shared resource is the rate limit, which
pauses new launches when the CLI says the window is gone. The run is
**resumable** — a contract already scored is skipped — so it is safe to stop it,
change a constant such as `MAX_TURNS`, and start again. A contract interrupted
mid-container writes nothing, so no half-judged contract enters `preds.csv`; kill
any orphaned `judge-*` containers before restarting.

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

# The experiment

**Question.** Given a contract and the provisions it contains, can a model say
which provisions a federal court would find something to construe?

Two runs answer it. They are given the **same question in the same words** and
differ only in how the material reaches the model:

| | `llm_api` | `agent` |
|---|---|---|
| shape | one Messages API call per contract | one Claude Code session per contract |
| the contract | inside the prompt | a file in a workspace |
| reading | one pass, in the order given | the model chooses — read, re-read, grep |
| answer | structured output | a file the model writes |
| model | `claude-opus-5`, effort `high` | `claude-opus-5`, effort `high` |
| billing | API key | Claude Code subscription |
| caching | none | on (an agent re-sends its transcript every turn) |

Both import the same taxonomy, the same worked examples, the same gold mapping
and the same provision-id scheme from `exp3_llm_api.py`, so the comparison cannot
drift between them. The judging criteria live in `prompts/exp3.md` and its
`SYSTEM` section is used **verbatim** by both.

---

## 1. Evaluation set

The 64 contracts of the dataset that are not used as worked examples:

```
6,461 clauses  |  134 positive  (2.1%)  |  64 contracts
type 1: 122 positive   type 2: 12 positive
```

Three contracts are held out permanently to supply the worked examples — one per
risk code present (1.1, 1.3, 2.2). They are the same three on every run and
appear in no evaluation.

---

## 2. What the model is given

**Worked examples — one pair per risk code.** Each pair is two real provisions
from the same contract: one a court construed, together with **the passage of the
opinion showing the dispute**, and one no court construed in that case. The
second is labelled **LOWER RISK**, never clean, because that is all the data
supports.

Each example states its contract's full count — how many provisions a court
construed and how many it did not (3/95, 4/251, 3/18) — framed as the base rate
to expect, not a quota to reproduce.

**The contract**, in full.

**The provisions to judge**, under opaque ids `c001…cNNN` assigned in order of
appearance in the contract. The dataset's own `pos1`/`neg1` ids never reach the
model: they would put the gold label on the door of every provision and group the
answers at the top of the list. Document order is also the order a reader meets
them in, and it places each provision beside its neighbours — which is what a
Category 2 judgement needs. The mapping back is stored per contract in the raw
output and applied before anything reaches the predictions file.

---

## 3. What the model returns

Per provision, two **independent** probabilities in [0, 1] to two decimal places
on a 0.01 grid:

- `prob_cat1` — an intrinsic textual defect, visible in the provision itself
  (taxonomy 1.x);
- `prob_cat2` — a defect in the provision's relationship to the rest of the
  instrument (2.x).

They need not sum to 1: a provision may carry neither, one or both. Each comes
with two sentences of reasoning, `reasoning_cat1` assessing only the wording and
`reasoning_cat2` naming the specific other provision that was checked.

**Every provision of a contract is judged in one call or one session.** Category
2 asks about the relationship between provisions, so splitting a contract into
batches would remove the very context the question is about.

---

## 4. Handling a short answer

A model that returns 61 judgments when 359 were asked for has not answered the
question, and scoring the other 298 as "not risky" would measure the harness
rather than the model. So a short answer is always followed up, with no flag to
disable it:

- `llm_api` continues the **same conversation** — its own answer is replayed as
  an assistant turn and it is asked for exactly the ids it left out;
- `agent` **resumes the same session**, so "these ids are missing" is something it
  can check against what it already wrote.

Rounds stop as soon as one returns nothing new. Anything still unjudged after
that is scored as `not_risky` at probability 0 — unflagged at every threshold —
and reported, never silently dropped.

The agent writes its answers by hand and eventually writes a file that is not
valid JSON, usually by quoting the contract verbatim inside a reasoning string
without escaping the inner quotes. Such a file is read object-by-object, and an
object that still will not parse is read field-by-field with patterns anchored on
the next key. Nothing is invented: a judgment without both probabilities is
dropped.

---

## 5. Isolating the agent run

The one-shot run is a stateless API call with nothing to isolate. The agent run
spawns the Claude Code CLI, which can load settings files, `CLAUDE.md`, memory,
skills and MCP servers that never appear in the conversation.

Every session runs under:

| | |
|---|---|
| `setting_sources=[]` | the SDK's isolation mode: no settings file, no `CLAUDE.md` |
| `skills=[]` | `None` means "the SDK configures nothing", not "skills off" |
| `strict_mcp_config=True` | ignore project, user and plugin MCP configuration |
| `tools` / `allowed_tools` | `Read`, `Grep`, `Glob`, `Write` |
| `disallowed_tools` | `Bash`, `Edit`, `Task`, `Skill`, `WebFetch` and the rest, named explicitly |
| `PreToolUse` hook | every path resolved against the workspace; anything outside is refused |

Auto memory, `CLAUDE.md` injection, the auto-updater and the CLI's non-essential
traffic sit outside `setting_sources` and are disabled by sweeping the parent
process's environment before the SDK spawns anything. `ClaudeAgentOptions.env`
cannot do this — it merges over the inherited environment and can only add.

`preflight.py` runs one real session under these options and audits its
trajectory for memory, `CLAUDE.md`, skills, `mcp__*` tools, free-standing
`<system-reminder>` blocks, stray models, CLI version drift, out-of-scope tools
and paths outside the workspace. It exits non-zero on any failure and runs before
the full run.

Provenance goes in `run_manifest_<stamp>.json` beside the session logs: SDK and
CLI versions (the CLI is the one bundled in the SDK wheel, which the SDK spawns
in preference to anything on `PATH`), interpreter, platform, git commit, the
names of every environment variable removed, the options verbatim, and SHA-256 of
the prompts and of `dataset.csv`.

`REPRODUCIBILITY.md` covers each point in detail.

---

## 6. How it is scored

Three binary tasks, each swept over a flagging threshold t in [0, 1]:

| panel | score | positive when |
|---|---|---|
| risky vs not | `max(prob_cat1, prob_cat2)` | the court construed the clause at all |
| type 1 vs not | `prob_cat1` | the construction turned on an intrinsic defect |
| type 2 vs not | `prob_cat2` | it turned on the clause's fit with the instrument |

The two type panels are **one-vs-rest**: for type 1 a type-2 clause counts as a
negative, and the other way round. That is the question the two probabilities are
actually asked — each is an independent judgement about its own category, not a
share of one distribution.

Reported per panel: **ROC-AUC** for ranking quality without picking a threshold,
then **precision**, **recall** and **the share of clauses flagged** against
threshold. The flag rate is what stops the first two being read too kindly — at
2% prevalence, a threshold that flags a third of the contract can still post a
respectable recall. `compare_exp3.py` prints these; `plot_exp3_thresholds.py`
draws the sweep.

Runs are compared only on the clauses **both** have scored, joined on
`(contract_id, clause_id)`. A partial run against a full one would differ as much
in which contracts each covered as in anything about the method.

---

## 7. Artifacts

Named in parallel, with `<run>` being `llm_api` or `agent`:

```
output/exp3_<run>_preds.csv                    one row per provision, both runs
output/exp3_<run>/<cid>.json                   returned judgments + the id map
output/llm_logs/exp3_<run>/<cid>.json          request, response, token usage
output/llm_logs/exp3_agent/<cid>.trajectory.jsonl  every tool call and thinking
                                               block of the session
output/figures/exp3_<run>_threshold_curves.png the three panels
output/exp3_agent_ws/<cid>/                    the agent's workspace, kept
```

`preds.csv` is append-only. A row with `ok=0` is a provision that came back
unjudged; where both exist for one provision, readers prefer the scored row.

---

## 8. Results

**Pending.** The agent arm is being rerun under the corrected harness of §5. The
`agent` figures below are from the superseded run, kept on
`legacy_agent_experiment_8.17`, and are not to be cited. The `llm_api` column is
unaffected.

Both runs cover all 6,461 clauses of all 64 contracts with no provision left
unjudged on either side, so the comparison is like-for-like on identical clauses
under identical ids.

| | ROC-AUC | P@0.5 | R@0.5 | flagged |
|---|---:|---:|---:|---:|
| `llm_api` | 0.869 | 0.21 | 0.46 | 4.6% |
| `agent` *(superseded)* | 0.914 | 0.27 | 0.66 | 5.0% |

Per category, one-vs-rest — ROC-AUC:

| | `llm_api` | `agent` *(superseded)* | positives |
|---|---:|---:|---:|
| type 1 — intrinsic defect | 0.863 | 0.909 | 122 |
| type 2 — relational defect | 0.958 | 0.960 | 12 |

What each recall target costs on the risky-vs-not panel:

| recall | | threshold | precision | flagged |
|---:|---|---:|---:|---:|
| 70% | `llm_api` | 0.37 | 0.088 | 16.7% |
| 70% | `agent` *(superseded)* | 0.47 | 0.224 | 6.6% |
| 80% | `llm_api` | 0.31 | 0.059 | 28.9% |
| 80% | `agent` *(superseded)* | 0.40 | 0.106 | 16.0% |

Full sweeps in `output/figures/exp3_<run>_threshold_curves.png`: precision and
recall on top, flag rate underneath, for each of the three panels.

**Shared weakness.** Both rank type 2 well (ROC ≈ 0.96) and neither is usable as
an absolute probability for it — 12 positives is too few to calibrate against.

### Cost

| | calls | input | cache-read | output | |
|---|---:|---:|---:|---:|---:|
| `llm_api` | 65 | 3,960,394 | 0 | 1,265,372 | **$51.44** |
| `agent` *(superseded)* | 64 (816 turns) | 18,979 | 46,599,371 | 1,642,032 | $121.81 API-equivalent |

The agent run is billed to a Claude Code subscription, so its dollar figure is
what the same tokens would have cost through the API, not an amount charged.
Caching absorbed 99.6% of its input: an agent re-sends its transcript every turn,
and without caching the 46.6M cache-read tokens would have been billed in full.

---

## 9. Running it

```bash
python src/experiments/exp3_llm_api.py --shuffle          # API key
python src/experiments/exp3_agent.py   --shuffle          # subscription
python src/experiments/plot_exp3_thresholds.py --run llm_api
python src/experiments/plot_exp3_thresholds.py --run agent
```

Both are resumable: a contract already scored is skipped, and any provision left
unjudged by an earlier run is finished before new contracts are started.
`--shuffle` runs the contracts in a seeded random order rather than largest
first, so the first N are a fair sample of the corpus instead of the N longest
documents. `--dry-run` on `exp3_llm_api.py` prices the outstanding calls exactly,
by building every prompt and metering the largest.

The agent run reads its rate-limit state from the CLI and waits out an exhausted
window. There is no other pacing: it starts the next contract as soon as the
previous one is written.

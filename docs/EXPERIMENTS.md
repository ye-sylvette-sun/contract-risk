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

**Every session runs in its own container**, and that is the first line of
defence rather than the last. The image has no `~/.claude`, no user `CLAUDE.md`,
no skills, no MCP registration and no `/etc/claude-code/managed-settings.json`
— managed policy being the one channel an environment sweep cannot reach. Only
the contract's own workspace is mounted, so `dataset.csv` and the other 63
contracts are not on the filesystem at all. The two prompts are mounted
read-only outside the workspace, so the workspace holds exactly what the model
is meant to see.

The options below are still applied, but they are now belt-and-braces:

| | |
|---|---|
| `setting_sources=[]` | the SDK's isolation mode: no settings file, no `CLAUDE.md` |
| `skills=[]` | `None` means "the SDK configures nothing", not "skills off" |
| `strict_mcp_config=True` | ignore project, user and plugin MCP configuration |
| `tools` / `allowed_tools` | `Read`, `Grep`, `Glob`, `Write` |
| `disallowed_tools` | `Bash`, `Edit`, `Task`, `Skill`, `WebFetch` and the rest, named explicitly |
| `PreToolUse` hook | every path-shaped argument resolved against the workspace; anything outside is refused |

The hook covers `file_path`, `path`, `notebook_path`, **`pattern` and `glob`**.
The last two were once missing, and an audit found sessions issuing
`/tmp/**/contract.txt`, `/*/*/contract.txt` and
`/**/<other-contract>*/contract.txt` that the hook never saw. Nothing escaped —
they returned "No files found" or hit ripgrep's timeout — but that is
containment by filesystem layout, not by construction.
`src/experiments/test_isolation.py` now pins all 22 cases, costs nothing, and
runs on host and container alike.

Auto memory, `CLAUDE.md` injection, the auto-updater and the CLI's non-essential
traffic sit outside `setting_sources` and are disabled by sweeping the
environment **inside the container** before the SDK spawns anything.
`ClaudeAgentOptions.env` cannot do this — it merges over the inherited
environment and can only add. In a container the sweep typically removes
nothing, because there is nothing to remove; on a developer machine it removed
eleven variables.

`preflight.py` runs one real session under these options and audits its
trajectory for memory, `CLAUDE.md`, skills, `mcp__*` tools, free-standing
`<system-reminder>` blocks, stray models, CLI version drift, out-of-scope tools
and paths outside the workspace. It exits non-zero on any failure and runs before
the full run.

Provenance goes in `run_manifest_<stamp>.json` beside the session logs: the
image id, SDK and CLI versions (the CLI is the one bundled in the SDK wheel,
which the SDK spawns in preference to anything on `PATH` — the image build
asserts the bundled version), interpreter, platform, git commit, the options
verbatim, and SHA-256 of the Dockerfile, the entrypoint, `isolation.py`, the
prompts and `dataset.csv`.

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

Both runs cover all 6,461 clauses of all 64 contracts with no provision left
unjudged on either side, so the comparison is like-for-like on identical clauses
under identical ids. Full write-up in [REPORT.md](REPORT.md).

| panel | | ROC-AUC | PR-AUC | P@0.5 | R@0.5 | flagged |
|---|---|---:|---:|---:|---:|---:|
| risky vs not | `llm_api` | 0.869 | 0.312 | 0.21 | 0.46 | 4.6% |
| risky vs not | **`agent`** | **0.891** | **0.385** | **0.25** | **0.54** | 4.5% |
| type 1 — intrinsic | `llm_api` | 0.863 | 0.303 | 0.25 | 0.40 | 3.0% |
| type 1 — intrinsic | **`agent`** | **0.890** | **0.358** | **0.31** | **0.48** | 3.0% |
| type 2 — relational | `llm_api` | **0.958** | 0.139 | 0.04 | 0.42 | 2.1% |
| type 2 — relational | `agent` | 0.942 | **0.220** | 0.04 | **0.58** | 2.5% |

At 2.1% prevalence PR-AUC is the number to read; ROC-AUC is flattered by the
6,327 easy negatives. The agent arm leads on PR-AUC in all three panels at an
identical flag rate — its extra recall is a better-chosen 4.5%, not a larger one.

What each recall target costs on the risky-vs-not panel:

| recall | | threshold | precision | flagged |
|---:|---|---:|---:|---:|
| 70% | `llm_api` | 0.37 | 0.088 | 16.7% |
| 70% | **`agent`** | 0.42 | **0.122** | **12.1%** |
| 80% | `llm_api` | 0.31 | 0.059 | 28.9% |
| 80% | **`agent`** | 0.35 | **0.077** | **21.9%** |
| 90% | `llm_api` | 0.25 | 0.047 | **39.9%** |
| 90% | `agent` | 0.27 | 0.046 | 41.7% |

Full sweeps in `output/figures/exp3_<run>_threshold_curves.png`: precision and
recall on top, flag rate underneath, for each of the three panels.

**Shared weakness.** Both rank type 2 well (ROC ≈ 0.95) and neither is usable as
an absolute probability for it — precision at 0.5 is 0.04 either way, on 12
positives.

**One run per arm.** No temperature or seed is set, run-to-run variance is not
measured, and the gaps above are not known to exceed it. See §5 of
[REPORT.md](REPORT.md).

### Cost

| | calls | input | cache-read | output | |
|---|---:|---:|---:|---:|---:|
| `llm_api` | 65 | 3,960,394 | 0 | 1,265,372 | **$51.44** |
| `agent` | 64 (847 turns) | 1,278 | 34,300,221 | 1,537,402 | $109.31 API-equivalent |

The agent run is billed to a Claude Code subscription, so its dollar figure is
what the same tokens would have cost through the API, not an amount charged.
Caching absorbed 86% of its input: an agent re-sends its transcript every turn,
and without caching the 34.3M cache-read tokens would have been billed in full.
Per provision the agent spends 238 output tokens against the one-shot arm's 196.

---

## 9. Running it

### First by hand: the subscription credential

The one-shot arm bills the **API key** from `.env`. The agent arm bills the
Claude Code **subscription**, and every session runs in a container that has no
`~/.claude` to log in from — so the credential must be passed in from outside.

Run this in a **real terminal**, before anything else:

```bash
claude setup-token          # opens a browser; prints a long-lived (1-year) token
```

It cannot be scripted, and it is worth knowing why rather than rediscovering it:
the flow is a full-screen terminal prompt. Given a pipe instead of a TTY it
blocks forever with no output, and teeing its output to capture the token makes
it exit immediately after "Opening browser to sign in". Interactive and
capturable are mutually exclusive here. So: run it by hand, copy the token, and
put it in `.env` (loaded by `lib.py`), or export it in the shell you start the
run from — an exported value wins over `.env`.

```bash
CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...                   # in .env
export CLAUDE_CODE_OAUTH_TOKEN='sk-ant-oat01-...'          # bash / zsh
$env:CLAUDE_CODE_OAUTH_TOKEN = 'sk-ant-oat01-...'          # PowerShell
```

With that variable unset, `exp3_agent.py` falls back to bind-mounting
`~/.claude/.credentials.json` read-only, as a **single file** — never the
directory. The fallback works; it just cannot refresh a token that expires
mid-run, and four containers sharing one read-only file could not refresh it
safely anyway.

### Then the experiment

```bash
docker build -f docker/Dockerfile -t contract-risk-judge:0.2.139 .
python src/experiments/test_isolation.py                  # 22 cases, no cost
python src/experiments/preflight.py --container           # one real session, audited

python src/experiments/exp3_llm_api.py --shuffle --parallel 4        # API key
python src/experiments/exp3_agent.py --shuffle --parallel 4           # subscription
python src/experiments/plot_exp3_thresholds.py --run llm_api
python src/experiments/plot_exp3_thresholds.py --run agent
```

`--parallel` sets how many containers run at once; they are independent
sessions against one subscription, so the only shared resource is the rate
limit, which pauses new launches when the CLI says the window is gone.

Both are resumable: a contract already scored is skipped, and any provision left
unjudged by an earlier run is finished before new contracts are started.
`--shuffle` runs the contracts in a seeded random order rather than largest
first, so the first N are a fair sample of the corpus instead of the N longest
documents. `--dry-run` on `exp3_llm_api.py` prices the outstanding calls exactly,
by building every prompt and metering the largest.

The agent run reads its rate-limit state from the CLI and waits out an exhausted
window. There is no other pacing: it starts the next contract as soon as the
previous one is written.

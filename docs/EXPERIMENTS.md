# The experiments

Two run in sequence. **Risk detection** asks a model to rank the
provisions a court went on to construe above the rest. The **issue-alignment
check** then asks whether it did so for the reason the court actually had —
§10 — because ranking metrics cannot tell a right answer from a right answer
reached by the wrong route.

**Question.** Given a contract and the provisions it contains, can a model say
which provisions a federal court would find something to construe?

> **The `llm_api` arm has been retired and deleted.** This experiment once ran
> two arms — one stateless API call per contract against one agent session per
> contract — and compared them. **The agentic approach is now the experiment**:
> `risk_detect_agent.py` is what runs and what §8 reports. What the two arms
> shared — `FIELDS`, `pred_row`, `gold_types`, `anonymise`, `pick_examples`,
> `roc_auc` — is now `runs.py`, so a run and everything that scores it still
> cannot disagree about what a column means. Nothing calls the Anthropic API
> directly any more, so no `ANTHROPIC_API_KEY` is needed. The two-arm comparison
> is on `2026.9.1_legacy_spellbook`; the deleted arm on
> `2026.9.3_legacy_multi_issue_experiment`.

**The experiment stays on Claude**, while the dataset build moved to OpenAI. The
agent arm *is* the Claude Code CLI, so this is not a free choice.
`lib.provider_of()` reads the transport off the model name, which lets the build
and the experiment sit in one codebase without either drifting onto the other's
API.

How the material reaches the model:

| | `agent` |
|---|---|
| shape | one Claude Code session per contract, in a container |
| the contract | a file in a workspace |
| worked examples | one `notes.md` per pair in the workspace |
| reading | the model chooses — read, re-read, grep |
| answer | a file the model writes |
| model | `claude-opus-5`, effort `high`, ceiling 100 turns |
| billing | Claude Code subscription |
| caching | on (an agent re-sends its transcript every turn) |

The example material is the two provision texts and the court's verbatim words,
and nothing more; the examples' full contracts are deliberately absent from the
workspace. The judging criteria live in `prompts/risk_detect.md` and its `SYSTEM`
section is used **verbatim**.

---

## 1. Evaluation set

Every contract of the dataset that is not used as a worked example. One example
is picked per risk code present — three of them, drawn from **two** contracts,
since two codes happened to select clauses of the same document. Those two
contracts are held out permanently and appear in no evaluation.

```
11,921 clauses  |  220 positive  (1.8%)  |  101 contracts
300 issues — the defects the courts construed, counted one by one
type 1: 172 positive   type 2: 68 positive   (not exclusive)
```

The issue count is the denominator that matters for §10. A positive can carry
more than one defect, so "how many of the court's findings did the model reach"
is a different and larger question than "how many provisions did it flag".

Examples are picked per risk code, not per comma-joined taxonomy string — a
clause labelled `1.1,1.3` counts as a candidate for both codes. Getting that
wrong invents codes that do not exist and holds out more contracts than
intended.

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
appearance in the contract. Document order is the order a reader meets them in,
and it places each provision beside its neighbours — which is what a risk type 2
judgement needs. The mapping back is stored per contract in the raw output and
applied before anything reaches the predictions file.

The dataset's own ids are now positional too (`c001…`, assigned by step 1 before
anything knew the label), so this renumbering is no longer what stands between
the model and the answer key. It was: an earlier build used `pos1`/`neg1`, which
put the gold label on the door of every provision. Keeping the renumbering costs
nothing and keeps the experiment independent of how the dataset happens to key
its rows.

---

## 3. What the model returns

Per provision, an **issue list** — not a pair of numbers. One entry per distinct
defect a court could be asked to construe:

| field | |
|---|---|
| `issue` | two sentences naming **this** defect — the ambiguous term, or the other provision it cannot be squared with. Never null: an entry exists only because there is a defect to name |
| `type` | `1` intrinsic, visible in the provision itself (taxonomy 1.x); `2` relational, about its fit with the rest of the instrument (2.x) |
| `prob` | in (0, 1], two decimal places on a 0.01 grid, that a court would construe the provision **on account of this issue** |

A pair of numbers could not say that a provision carries a vague term *and* a
list-scope problem *and* contradicts a definition three sections away — three
things of different strengths. The list can, and each strength is stated
separately.

**Most lists are short, and that is asked for explicitly.** The normal answer is
an **empty list**: the provision was read and nothing nameable was found in it.
Next most common is a single named issue, of whichever type it is, with no entry
for the other. Two issues of one type, or issues of both, are the exception and
are to be given only when the extra defect is obvious and carries a high
probability in its own right — the prompt's test is whether the second defect
could be stated to a judge on its own and taken seriously.

**A weak defect you can name is a low-probability entry, not an empty list, and
the prompt has to say so in those words.** The first wording of this contract did
not. It said an empty list was the ordinary answer, that it was not a failure to
judge, and that it "costs you nothing"; and, having removed the null channel, it
also lost the old prompt's push to name defects one could not prove. The 51-
contract run that followed measured the cost. Named issues fell from 0.80 per
provision to 0.30, and **18 of 92 positives (20%) came back with an empty list**
— scoring exactly 0, tied with 71% of the corpus and unrecoverable at any
threshold, which caps recall at 80%. Under the previous wording that figure was
0 of 23, across two runs.

The cause is not sample difficulty, and the comparison that shows it is
controlled: twelve contracts were judged under both wordings, and the rate of
provisions with no named issue rose in **twelve of twelve** (mean 39% → 61%). One
missed positive appears in both runs — `252FSupp3d52_agreement_for_network_
operator/c013` — and under the old wording the same model named two defects on
it, including a type-2 entry at 0.24 that identified the mechanism precisely.
Reading all 18 by hand, roughly eight are plainly nameable 1.1 ambiguities: the
clearest is a 218-character arbitration clause reciting "judgment may be entered
… in accordance with **applicable law**" without saying which law, which is what
the court construed. The model could see these; the wording talked it out of
saying so. Hence the current phrasing: uncertainty goes into the probability,
inability to name goes into the empty list, and the question separating them is
not "am I confident?" but "can I say what is wrong?"

**A type with no entry scores 0, and that is what it means.** An earlier version
of this contract asked instead for a null-text entry per absent type, to carry
"how likely that kind of dispute is anyway" — the worry being that a bare 0 was
a stronger claim than the answer meant. The two runs that used it settled the
question: across 3,317 such entries the model never put one above 0.24, while
named issues ran to 0.81, and in 1,275 provisions carrying a named issue a null
entry outranked every named one exactly once. Forcing them to 0 moved ROC-AUC by
−0.003, 0.000 and 0.000 on the three panels. The channel was costing a third of
the output to say nothing, so it is gone.

**An empty list is an answer; an absent provision is not.** `predictions.valid()`
accepts an explicit `[]` and rejects a judgment whose issues are missing or
unreadable, so a truncated file still goes to the top-up round instead of
passing as a page of clean verdicts. The salvage path matches `"issues": []`
literally rather than inferring emptiness from a failed parse, which would
collapse the same distinction.

**Scoring derives two numbers from the list**: `prob_type1` and `prob_type2` are
the **strongest** issue of each type. Not a sum — the probabilities are per issue
and independent, so two weak issues must not add up to a likely dispute, and what
each panel asks is whether a court would construe the provision on account of
*any* of them. `predictions.probs_of()` is the single implementation, shared by
the run, the container and the Spellbook scorer.

**Every provision of a contract is judged in one call or one session.** Risk
type 2 asks about the relationship between provisions, so splitting a contract
into batches would remove the very context the question is about.

---

## 4. Handling a short answer

A model that returns 61 judgments when 359 were asked for has not answered the
question, and scoring the other 298 as "not risky" would measure the harness
rather than the model. So a short answer is always followed up, with no flag to
disable it:

`agent` **resumes the same session**, so "these ids are missing" is something it
can check against what it already wrote.

Rounds stop as soon as one returns nothing new. Anything still unjudged after
that is scored as `not_risky` at probability 0 — unflagged at every threshold —
and reported, never silently dropped.

The agent writes its answers by hand and eventually writes a file that is not
valid JSON, usually by quoting the contract verbatim inside an `issue` string
without escaping the inner quotes. Such a file is read object-by-object, and an
object that still will not parse is read **issue by issue**, with patterns
anchored on the next key and accepting either field order — an agent writing by
hand does not always follow the schema's. Nothing is invented: a judgment no
issue could be read out of is dropped, since scoring it would mean inventing a
probability.

---

## 5. Isolating the agent run

The agent run spawns the Claude Code CLI, which can load settings files,
`CLAUDE.md`, memory, skills and MCP servers that never appear in the
conversation. (The retired one-shot arm was a stateless API call with nothing to
isolate — this section is the price of the agentic shape.)

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
| risky vs not | `max(prob_type1, prob_type2)` | the court construed the clause at all |
| risk type 1 vs not | `prob_type1` | `gold_type1` — the construction turned on an intrinsic defect |
| risk type 2 vs not | `prob_type2` | `gold_type2` — it turned on the clause's fit with the instrument |

The two risk-type panels are **one-vs-rest**, and the two types are **not
exclusive** on either side of the comparison. A clause whose case was filed under
several Westlaw keys can be gold for both, and then counts as a positive in both
panels; the model likewise returns an independent probability for each. That is
why there is no single 3-way confusion matrix — a winner-takes-all table would
have to invent an ordering between two judgements that were never asked to
compete.

Reported per panel: **ROC-AUC** for ranking quality without picking a threshold,
then **precision**, **recall** and **the share of clauses flagged** against
threshold. The flag rate is what stops the first two being read too kindly — at
2% prevalence, a threshold that flags a third of the contract can still post a
respectable recall. `compare_risk_detect.py` prints these; `plot_risk_detect_thresholds.py`
draws the sweep.

`compare_risk_detect.py` scores the run. Given `--against PATH` it scores two,
but only on the clauses **both** have covered, joined on `(contract_id,
clause_id)` — a partial run against a full one would differ as much in which
contracts each covered as in anything about the method. That is how two repeats
of one prompt, or two prompts, are compared; there is no second arm any more.
`plot_risk_detect_thresholds.py` produces the reported figure.

---

## 7. Artifacts

Named with `<run>` being `agent`:

```
output/risk_detect_<run>_preds.csv                    one row per provision
                                               (carries the whole issue list in
                                               `issues`, plus the derived
                                               `prob_type1` / `prob_type2`)
output/risk_detect_<run>/<cid>.json                   returned judgments + the id map
output/llm_logs/risk_detect_<run>/<cid>.json          request, response, token usage
output/llm_logs/risk_detect_agent/<cid>.trajectory.jsonl  every tool call and thinking
                                               block of the session
output/figures/risk_detect_<run>_threshold_curves.png the three panels
output/risk_detect_agent_ws/<cid>/                    the agent's workspace, kept
```

`preds.csv` is append-only. A row with `ok=0` is a provision that came back
unjudged; where both exist for one provision, readers prefer the scored row.
`runs.pred_row()` is the only place a row is built, so a run and everything
that scores it cannot disagree about what a column means.

---

## 8. Results

**Not yet run on the current dataset.** The dataset was rebuilt when step 1 and
step 2 were reordered (see [DATASET.md](DATASET.md) §3): clause boundaries, the
positive set and the ids all changed, so the previous run's `preds.csv` cannot
be joined to it and its numbers are not comparable. The run has to be repeated.

The superseded run and its write-up are on
`2026.9.3_legacy_multi_issue_experiment`. For the record, so that the repeat has
something to be read against — over 11,636 provisions of 100 contracts, 100
sessions, 1,254 turns, nothing left unjudged:

| panel | positives | ROC-AUC | PR-AUC | P@0.5 | R@0.5 | flagged |
|---|---:|---:|---:|---:|---:|---:|
| risky vs not | 190 | 0.899 | 0.361 | 0.35 | 0.53 | 2.5% |
| risk type 1 — intrinsic | 141 | 0.898 | 0.326 | 0.31 | 0.50 | 2.0% |
| risk type 2 — relational | 81 | 0.854 | 0.149 | 0.21 | 0.22 | 0.7% |

Bootstrap 95% CI on the main panel was [0.844, 0.949]. Cost was 1,890 input,
8.6M cache-create, 55.8M cache-read and 2.4M output tokens over 7.6 hours of
container time — **$174.35 at API-equivalent rates**, billed to a subscription
rather than charged; caching absorbed 85% of the input side. Expect the repeat
to cost about the same: the evaluation set is 11,921 provisions against 11,636.

**Read the repeat against the length baseline, not against the table above.**
Length alone now ranks provisions at within-contract ROC-AUC **0.706**, where
the superseded build gave 0.523. That is not a regression in the pipeline —
DATASET.md §6 sets out why the old figure was the artifact — but it does mean a
headline AUC has a much higher floor to clear than it did, and the two builds'
numbers must not be put side by side as though they measured the same thing.

**Run-to-run variance is unquantified**: no seed, no temperature control. One
repeat under an earlier design moved ROC-AUC by ~0.02 and recall@0.5 by ~0.10,
which is the scale against which small differences should be judged.
`compare_risk_detect.py --against` exists to measure this properly and has not
been used for it yet.

---

## 9. Running it

### First by hand: the subscription credential

The agent arm bills the Claude Code **subscription**, and every session runs in a
container that has no `~/.claude` to log in from — so the credential must be
passed in from outside. No `ANTHROPIC_API_KEY` reaches the container: `docker
run` is given only `-e CLAUDE_CODE_OAUTH_TOKEN`, and `isolation.env()` sweeps
every `ANTHROPIC_*` variable inside the container before the SDK spawns.

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

With that variable unset, `risk_detect_agent.py` falls back to bind-mounting
`~/.claude/.credentials.json` read-only, as a **single file** — never the
directory. The fallback works; it just cannot refresh a token that expires
mid-run, and four containers sharing one read-only file could not refresh it
safely anyway.

### Then the experiment

```bash
docker build -f docker/Dockerfile -t contract-risk-judge:0.2.139 .
python src/experiments/test_isolation.py                  # 22 cases, no cost
python src/experiments/preflight.py --container           # one real session, audited

python src/experiments/risk_detect_agent.py --shuffle --parallel 8   # the experiment
python src/experiments/plot_risk_detect_thresholds.py --run agent
```

`--parallel` sets how many containers run at once; they are independent
sessions against one subscription, so the only shared resource is the rate
limit, which pauses new launches when the CLI says the window is gone.

The run is **resumable**: a contract already scored is skipped, and any provision
left unjudged by an earlier run is finished before new contracts are started.
This is what makes it safe to stop the run, change a constant such as
`MAX_TURNS`, and start it again — completed contracts are not re-billed. A
contract interrupted mid-container writes nothing, so no half-judged contract can
enter `preds.csv`; kill any orphaned `judge-*` containers before restarting.

`--shuffle` runs the contracts in a seeded random order rather than largest
first, so the first N are a fair sample of the corpus instead of the N longest
documents.

The agent run reads its rate-limit state from the CLI and waits out an exhausted
window. There is no other pacing: it starts the next contract as soon as the
previous one is written.

---

## 10. The issue-alignment check

Risk detection scores a hit when the model ranks a litigated provision high. It
cannot see whether the **defect the model named** is the one the court
construed. A model that flags the right provision for the wrong reason scores
exactly as well as one that understood the dispute, and a reviewer acting on the
first would look in the wrong place.

`issue_alignment_check.py` closes that gap. For each issue the agent named, a
**different** model reads the provision, the risk-type definition, the issue,
and **the defects step 2 recorded for that provision** — each with its own
verbatim passage — and says which of them the named defect is, and how closely.

**The judge names the match, and that is what makes recall computable.** Step 2
records every defect a court construed separately, so `matched` identifies which
one an issue found. Two numbers follow instead of one:

| | |
|---|---|
| precision | of the issues the agent named, how many name a defect the court construed |
| recall | of the 300 defects in the evaluation set, how many the agent found |

Recall did not exist before. The previous build recorded one passage per
provision, so several distinct defects collapsed into one target, and what was
reported as recall was really target coverage — an upper bound on the real
thing. Two issues that match the same defect count once.

**The judge is `gpt-5.6-sol`, effort high** — deliberately not the family being
judged, since the predictions came from `claude-opus-5` and a same-family judge
invites a self-preference objection. `lib.provider_of()` already routes by model
name, so this costs nothing structurally.

**What the judge is shown, and what it is not.** The provision, the type
definition, the issue text, and the candidate defects with their passages.
**Not** the agent's probability, which would anchor it, and not whether the
provision is gold — that is settled before the call and is not the question.

One further precaution: the one-line summary of each candidate defect was
written by step 2, which is the same model family as the judge. The prompt
therefore states that the summary is a pointer and **the passage is the
evidence**, and that the passage governs where the two disagree.

**Scope.** Only named issues whose provision *and* risk type both match gold. An
issue on a provision no court construed has no defect to check against; an issue
of the wrong type is already counted wrong by the one-vs-rest panels. So
precision here is **conditional** on the provision and the type being right —
not a second shot at the ranking.

Recall is not conditional in the same way: its denominator is every defect in
the evaluation set, including those on provisions the agent said nothing about.
A defect the check could never reach — no issue of that type was named on that
provision at any probability — caps recall, and the figure draws that ceiling.
Those are risk-detection misses, not wrong reasons, and the report separates
them.

**Output shape.**

| field | |
|---|---|
| `court_defect` | what the candidate passages show the court construing, written from them alone |
| `matched` | the id of the one candidate defect the issue names, or empty for none |
| `evidence` | a span copied **exactly** from the matched candidate's passage — the same verbatim discipline step 1 uses; no quote means no evidence |
| `determinable` | false when the passages are cut mid-argument, in which case `alignment` is forced to 0 |
| `alignment` | 0–1, how far the named defect is the matched defect |
| `reason` | one or two sentences tying the quote to the score |

An empty `matched` scores 0 whatever the model put in the field, as does a
`matched` naming a candidate that was not shown — an id outside the list means
the answer is not about the material, which is not a near miss to be salvaged.
The judge returns a score, never a verdict, and the 0.5 threshold is applied
when reporting. It is never shown to the judge, so the operating point can be
moved without re-running anything.

### Two nulls, because the instrument has to be validated

An LLM judge that says "aligned" to everything would produce a flattering number
and mean nothing. `--control` re-pairs every issue with candidate defects that
are not its own:

- **`corpus`** — candidates from a **different case**. Parties, subject matter
  and vocabulary all differ. This catches a judge that merely recognises legal
  prose.
- **`case`** — candidates from the **same case, a different provision**. Parties,
  instrument and vocabulary are shared and only the defect differs, so passing
  this means the judge discriminates between *defects*, not between *documents*.
  Its expected rate is **not zero**: a court that construes two provisions
  together can genuinely reach the same defect in both, which makes it a noise
  floor rather than a pure null.

Both write outside `output/` — a throwaway experiment should leave no artifact
behind:

```bash
python src/experiments/issue_alignment_check.py --control corpus --out /tmp/ctl
python src/experiments/issue_alignment_check.py --control case   --out /tmp/ctl
```

### The diagnostic that was removed

`--full-opinion` re-judged a misaligned issue against the entire opinion, to
measure what was lost by recording **one** contiguous passage per clause. Step 2
now records a passage per *defect*, so the question it asked no longer maps onto
the data, and a diagnostic whose meaning has quietly changed is worse than none.
It was removed rather than left running.

What it found before removal is still the reason to expect little from more
context: of 78 misaligned issues re-judged against the whole opinion, only 9
flipped — a ceiling of about +4.7 points — and 25 scored *lower*, because a whole
opinion is mostly about other provisions.

Results in [REPORT.md](REPORT.md) §9.

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

## 5. How it is scored

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

Reported per panel: precision and recall against threshold, **and the share of
clauses flagged**. The flag rate is what stops the first two being read too
kindly — at 2% prevalence, a threshold that flags a third of the contract can
still post a respectable recall. Ranking quality is reported threshold-free as
ROC-AUC and PR-AUC; with 2% positives, **PR-AUC is the number that matters**.

Runs are compared only on the clauses **both** have scored, joined on
`(contract_id, clause_id)`. A partial run against a full one would differ as much
in which contracts each covered as in anything about the method.

---

## 6. Artifacts

Named in parallel, with `<run>` being `llm_api` or `agent`:

```
output/exp3_<run>_preds.csv                    one row per provision, both runs
output/exp3_<run>/<cid>.json                   returned judgments + the id map
output/llm_logs/exp3_<run>/<cid>.json          request, response, token usage
output/figures/exp3_<run>_threshold_curves.png the three panels
output/exp3_agent_ws/<cid>/                    the agent's workspace, kept
```

`preds.csv` is append-only. A row with `ok=0` is a provision that came back
unjudged; where both exist for one provision, readers prefer the scored row.

---

## 7. Results

Both runs cover all 6,461 clauses of all 64 contracts, with **no provision left
unjudged on either side**, so this is a like-for-like comparison on identical
clauses under identical ids.

| | ROC-AUC | PR-AUC | P@0.5 | R@0.5 | flagged | best F1 | at t |
|---|---:|---:|---:|---:|---:|---:|---:|
| `llm_api` | 0.869 | 0.311 | 0.21 | 0.46 | 4.6% | 0.35 | 0.57 |
| `agent` | **0.914** | **0.451** | 0.27 | **0.66** | 5.0% | **0.48** | 0.56 |

Per category, one-vs-rest:

| | `llm_api` ROC / PR | `agent` ROC / PR | positives |
|---|---|---|---:|
| type 1 — intrinsic defect | 0.863 / 0.302 | 0.909 / 0.425 | 122 |
| type 2 — relational defect | 0.958 / 0.140 | 0.960 / 0.240 | 12 |

**The agent run is better on every headline measure.** PR-AUC is ~45% higher, and
at a nearly identical flag rate it finds two thirds of the litigated clauses
against under a half. Both runs put their best operating point in the same place
(t ≈ 0.56), so the gap is not an artefact of threshold choice. The margin held
steady from the first 18 shared contracts to all 64.

**Why**, from the session transcripts: it is not simply that the agent can search.
On the contract where it wins most it made eight tool calls and never grepped —
it read the same contract, the same examples and the same provision list the
one-shot call was handed. What differs is that the work is split across 12.8 turns
on average, each with its own reasoning budget and a narrow focus, and each batch
of answers written just after the relevant part of the contract was re-read. The
one-shot call must emit every judgment in a single generation after one pass; on a
68-provision contract that is 247 output tokens per provision against the agent's
376. Search does appear, but only on the largest documents — on the 418-provision
credit agreement the agent ran 4 greps batch-checking defined terms.

That shows up in the reasoning text. For one clause a court did construe, the
one-shot answer named a single cross-reference and scored 0.27; the agent named
four (`B.1.a of CP 10 30`, `Loss Payment E.4.b`, `Valuation E.7.b`,
`Replacement Cost G.3.f`) and scored 0.63. Category 2 asks about relationships,
and how many relationships get checked tracks the budget spent per provision.

**Shared weakness.** Both rank type 2 well (ROC ≈ 0.96) and calibrate it badly
(PR 0.14 / 0.24 on 12 positives). Neither is usable as an absolute probability for
that category.

### Cost

| | calls | input | cache-read | output | |
|---|---:|---:|---:|---:|---:|
| `llm_api` | 65 | 3,960,394 | 0 | 1,265,372 | **$51.44** |
| `agent` | 64 (816 turns) | 18,979 | 46,599,371 | 1,642,032 | $121.81 API-equivalent |

The agent run was billed to a Claude Code subscription, so its dollar figure is
what the same tokens would have cost through the API, not an amount charged.
Caching absorbed 99.6% of its input: an agent re-sends its transcript every turn,
and without caching the 46.6M cache-read tokens would have been billed in full.

---

## 8. Running it

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
window; `--rest-every N` paces it when the CLI reports nothing usable.

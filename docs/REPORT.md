# Does an agentic harness read a contract better than a single API call?

64 contracts, 6,461 provisions, 134 construed by a federal court (2.07%). Same
model (`claude-opus-5`, effort `high`), same prompt, same provision ids. The two
runs differ only in whether the contract arrives inside a prompt (`llm_api`) or
as a file in a workspace the model can re-read (`agent`). Design in
`EXPERIMENTS.md`, corpus in `DATASET.md`.

---

## 1. Results

Each provision gets a probability. Sweeping a flagging threshold over it trades
two quantities against each other:

- **recall** — of the 134 provisions a court actually construed, what share did
  we flag;
- **precision** — of everything we flagged, what share was actually construed;
- **flag rate** — what share of the contract a reader is asked to read. At 2%
  prevalence this is the number that keeps the other two honest: flag everything
  and recall is 1.0.

**ROC-AUC** and **PR-AUC** summarise the whole sweep in one number, without
picking a threshold. Both measure ranking — whether construed provisions score
above unconstrued ones. At 2% prevalence **PR-AUC is the decisive one**; ROC-AUC
stays flattering because the 6,327 negatives dominate it.

| | ROC-AUC | PR-AUC |
|---|---:|---:|
| `llm_api` | 0.869 | 0.302 |
| `agent` | **0.914** | **0.434** |

Full sweeps: `output/figures/exp3_llm_api_threshold_curves.png` and
`output/figures/exp3_agent_threshold_curves.png` — precision and recall on top,
flag rate underneath, for risky-vs-not and each risk type.

### At 80% recall

The practical question is what a reader pays to catch four fifths of the
litigated provisions:

| | threshold | precision | flagged | recall |
|---|---:|---:|---:|---:|
| `llm_api` | 0.31 | 0.059 | **28.9%** of the contract | 0.82 |
| `agent` | 0.40 | **0.106** | **16.0%** of the contract | 0.81 |

**Same recall, 1.8× the precision, and 45% less to read.** That is the result in
one line. It is not an artefact of threshold choice — the agent is ahead at every
recall target (at 70% recall it flags 6.6% against 16.7%).

Both are still weak in absolute terms: 0.106 precision means nine of ten flagged
provisions were never litigated. This is a triage aid, not a filter.

**Robustness.** Bootstrapping by contract, ΔROC is +0.046 (95% CI
[+0.001, +0.092]) and ΔPR +0.132 (95% CI [+0.006, +0.258]) — positive, but
barely clearing zero. Per contract the agent wins 23, loses 11, ties 14 (sign
test p = 0.058). The per-provision recall comparison is much stronger: 33
positives cross 0.5 only in the agent run against 6 only in the API run
(McNemar p = 0.00001). One caveat on that: 26 of the 33 were already at 0.30–0.49
in the API run, so the agent nudged them over a line rather than discovering
them.

---

## 2. Why is the agent better? Four hypotheses, three rejected

**H1 — it searches.** *No.* `Grep` was used 21 times across 64 sessions and never
in the median one. The agent reads the contract front to back — the same material
the one-shot call is handed, in the same order.

**H2 — it spends more compute.** *Not enough.* 1.32× the output tokens overall.
On contracts of 250+ provisions it spends 1.40× for a 3.4× PR-AUC difference
(0.661 vs 0.196). A 1.4× budget does not buy a 3.4× effect.

**H3 — the one-shot call degrades over a long generation.** *No.* Late judgments
should get shorter or more templated. They do not: within the longest contracts
the API's reasoning *grows* from 126 to 142 characters between the first and last
third, its distinct-word ratio is flat, and its exact-duplicate rate falls.

**H4 — batching lets it carry a cross-clause hypothesis.** *Weakly supported, and
the only one left.* The advantage concentrates on contracts with three or more
positives (mean ΔAUC +0.067, recall@0.5 of 69/99) and is near zero on contracts
with one or two (+0.034, +0.004).

The clearest case: a 359-provision collective bargaining agreement where the API
scored AUC 0.472 — worse than chance — and the agent 1.000. All five litigated
provisions turn on one term, *shift*. The API graded each locally and found each
one clear ("*precise on its face*"; "*specific, with a clear five-shift weekly
cap*"). The agent found the thread — that "shift" carries two senses in one
sentence of §5-A, and that neither "pay rate per shift" nor "shift worked" is
defined where §13-I.1 uses them to compute contributions. That is the dispute the
court took up. Every sentence the API wrote is individually true; the defect
exists only across five provisions at once.

### What the logs show it actually doing

816 turns and 755 tool calls over 64 sessions (median 10 turns, 9 tool calls).
The median session reads three worked-example notes, sweeps `contract.txt` in
windowed reads, then writes judgments in two batches of ~60 provisions, each
batch emitted just after the matching part of the contract was read. Self-repair
happened once in 755 tool calls; self-verification once. The capabilities that
make an agent an agent went almost entirely unused — what varied was how the
answer was emitted, not what was read.

---

## 3. Cost

| | calls | input | cache read | output | cost |
|---|---:|---:|---:|---:|---:|
| `llm_api` | 65 | 3,960,394 | 0 | 1,265,372 | **$51.44** |
| `agent` | 64 sessions / 816 turns | 18,979 | 46,599,371 | 1,642,032 | $121.81 API-equivalent |

The agent run was billed to a Claude Code subscription; its figure is what the
same tokens would have cost through the API, not an amount charged. Caching
absorbed 99.6% of its input — an agent re-sends its whole transcript every turn,
so without caching the 46.6M cache-read tokens would have been billed in full.
**About 2.4× the cost for +0.13 PR-AUC.**

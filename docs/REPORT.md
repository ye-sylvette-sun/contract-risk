# Experiment 3 — results

Two ways of asking one model the same question about the same 64 contracts:

- **`llm_api`** — one stateless API call per contract. The whole document and
  every provision go in; all judgments come back in one answer.
- **`agent`** — one Claude Code session per contract, in a container. The model
  gets a workspace (the contract, the provisions, three worked examples) and
  decides for itself what to read and in what order.

Same prompts, same worked examples, same model (`claude-opus-5`, effort high),
same provisions under the same ids. Both arms judged all 6,461 provisions of all
64 contracts with nothing left unjudged, so the comparison is like-for-like.

Prevalence is 2.1% — 134 litigated provisions in 6,461.

---

## 1. Headline

| panel | run | ROC-AUC | PR-AUC | P@0.5 | R@0.5 | flagged |
|---|---|---:|---:|---:|---:|---:|
| risky vs not | `llm_api` | 0.869 | 0.312 | 0.21 | 0.46 | 4.6% |
| risky vs not | **`agent`** | **0.891** | **0.385** | **0.25** | **0.54** | 4.5% |
| type 1 — intrinsic | `llm_api` | 0.863 | 0.303 | 0.25 | 0.40 | 3.0% |
| type 1 — intrinsic | **`agent`** | **0.890** | **0.358** | **0.31** | **0.48** | 3.0% |
| type 2 — relational | `llm_api` | **0.958** | 0.139 | 0.04 | 0.42 | 2.1% |
| type 2 — relational | `agent` | 0.942 | **0.220** | 0.04 | **0.58** | 2.5% |

At 2% prevalence **PR-AUC is the number to read**. ROC-AUC is flattered by the
6,327 easy negatives; PR-AUC is not. On that measure the agent arm is ahead on
every panel: +0.073 overall, +0.055 on type 1, +0.081 on type 2.

The flag rate is what keeps the rest honest. The agent flags **4.5%** of
provisions against the one-shot arm's 4.6% — so its higher recall is not bought
by flagging more, it is a better-chosen 4.5%.

## 2. What a recall target costs

The practical question is not "what is precision at 0.5" but "to catch most of
what was litigated, how much of the contract must a reader read".

| recall target | run | threshold | precision | share flagged |
|---:|---|---:|---:|---:|
| 70% | `llm_api` | 0.37 | 0.088 | 16.7% |
| 70% | **`agent`** | 0.42 | **0.122** | **12.1%** |
| 80% | `llm_api` | 0.31 | 0.059 | 28.9% |
| 80% | **`agent`** | 0.35 | **0.077** | **21.9%** |
| 90% | `llm_api` | 0.25 | 0.047 | **39.9%** |
| 90% | `agent` | 0.27 | 0.046 | 41.7% |

The agent's advantage is in the usable range and disappears at 90%. To catch
four fifths of litigated provisions it asks for 22% of the contract instead of
29% — about a quarter less reading. Past that, both arms degrade to flagging
roughly 40% of the document, which is not a useful tool either way.

## 3. Contract length

The agent works through a long contract in stages, which suggests it should hold
up better as documents grow. The evidence is weak and mixed:

| stratum | contracts | provisions | positives | dAUC (agent − api) | R api | R agent |
|---|---:|---:|---:|---:|---:|---:|
| short (6–119 prov) | 47 | 2,270 | 82 | +0.015 | 0.40 | 0.45 |
| medium (122–304) | 12 | 2,305 | 36 | +0.031 | 0.61 | **0.86** |
| long (332–418) | 5 | 1,886 | 16 | +0.031 | **0.38** | 0.25 |

The pooled AUC gap does grow with length, and the middle stratum shows the
largest recall gain of the study. But on the five longest contracts the agent's
recall is *worse*, and the per-contract correlation between contract size and
the agent's AUC advantage is only **r = +0.15** across 35 contracts. The long
stratum is 5 contracts and 16 positives.

"The agent scales better with document length" is a hypothesis this data is
consistent with, not a result it supports.

## 4. Cost

| | calls | input | cache-read | output | |
|---|---:|---:|---:|---:|---:|
| `llm_api` | 65 | 3,960,394 | 0 | 1,265,372 | **$51.44** |
| `agent` | 64 sessions, 847 turns | 1,278 | 34,300,221 | 1,537,402 | $109.31 API-equivalent |

The agent arm is billed to a Claude Code subscription; its dollar figure is what
the same tokens would have cost through the API, not an amount charged.

The agent costs about **2.1× more** for the numbers in §1. It also spends 1.21×
the output tokens — 238 per provision against 196 — which is the more meaningful
comparison, since the input side is dominated by an agent re-sending its
transcript every turn. Caching absorbed 86% of its input; without it the 34.3M
cache-read tokens would have been billed in full.

Whether 2.1× is worth a quarter less reading at 80% recall depends entirely on
what an hour of review time is worth against a dollar of inference.

---

## 5. Variance has not been accounted for

**This is one run per arm, and every comparison above should be read with that
in mind.**

Neither arm sets a temperature or a seed, and the API exposes no way to make
sampling deterministic. A rerun of either arm will not reproduce these numbers.
No repeated runs were made, so the run-to-run variance of each metric is
**unmeasured**, and no confidence interval, significance test or error bar in
this report accounts for it.

Two consequences for how the numbers should be used:

- The gaps in §1 are small in absolute terms — 0.02 in ROC-AUC, 0.07 in PR-AUC,
  0.08 in recall — and are not known to exceed what a second run of the same arm
  would produce on its own. **They should not be cited as a demonstrated
  ranking of the two methods.** The defensible statement is that the agent arm
  is at least as good as the one-shot arm and plausibly better, most clearly on
  PR-AUC and on the cost of a recall target.
- Precision and recall at a fixed 0.5 threshold are the most fragile numbers
  here. The mean predicted probability on gold positives sits very close to 0.5,
  so a small shift in calibration moves many positives across the line and
  swings recall substantially. The threshold-free ranking measures and the
  recall-cost curve of §2 are the more stable views and should be preferred when
  quoting a single figure.

The straightforward fix is repeated paired runs — three runs of each arm over a
fixed stratified subset would bound the variance and let every gap above be
stated as inside or outside it. It was not done here, on cost.

---

## 6. Summary

- The agent arm ranks litigated provisions better on PR-AUC across all three
  panels, at an identical flag rate, and reaches 80% recall for a quarter less
  reading. It costs about twice as much.
- The long-contract stratum points the way the agent design predicts, on too
  few positives to claim anything.
- None of these gaps has been tested against run-to-run variance, which is not
  measured.

Figures: `output/figures/exp3_llm_api_threshold_curves.png` and
`output/figures/exp3_agent_threshold_curves.png`. Numbers behind every table:
`output/exp3_comparison.json`.

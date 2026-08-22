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
| risky vs not | **`agent`** | **0.909** | **0.438** | **0.27** | **0.64** | 4.9% |
| category 1 — intrinsic | `llm_api` | 0.863 | 0.303 | 0.25 | 0.40 | 3.0% |
| category 1 — intrinsic | **`agent`** | **0.912** | **0.423** | **0.35** | **0.57** | 3.0% |
| category 2 — relational | `llm_api` | 0.958 | **0.139** | 0.04 | 0.42 | 2.1% |
| category 2 — relational | **`agent`** | **0.963** | 0.063 | 0.04 | **0.58** | 2.9% |

At 2% prevalence **PR-AUC is the number to read**. ROC-AUC is flattered by the
6,327 easy negatives; PR-AUC is not. The agent arm is ahead by +0.126 on the
main panel and +0.120 on category 1, and **behind by 0.076 on category 2** — see §4.

The strongest single line is category 1. Both arms flag **3.0%** of provisions, and
on that identical budget the agent recovers 57% of litigated provisions against
40%. No difference in threshold placement or in which provisions happened to be
easy explains that: it is the same amount of reading for 17 more points of
recall.

## 2. What a recall target costs

The practical question is not "what is precision at 0.5" but "to catch most of
what was litigated, how much of the contract must a reader read".

| recall target | run | threshold | precision | share flagged |
|---:|---|---:|---:|---:|
| 70% | `llm_api` | 0.37 | 0.088 | 16.7% |
| 70% | **`agent`** | 0.47 | **0.228** | **6.5%** |
| 80% | `llm_api` | 0.31 | 0.059 | 28.9% |
| 80% | **`agent`** | 0.41 | **0.123** | **13.6%** |
| 90% | `llm_api` | 0.25 | 0.047 | 39.9% |
| 90% | `agent` | 0.28 | 0.047 | 39.7% |

To catch four fifths of litigated provisions the agent asks for **13.6%** of the
contract against **28.9%** — less than half the reading, at twice the precision.
At 70% the ratio is wider still. The advantage closes completely at 90%, where
both arms flag about 40% of the document and neither is a useful tool.

## 3. Contract length

The agent works through a long contract in stages, which suggests it should hold
up better as documents grow.

| stratum | contracts | provisions | positives | dAUC (agent − api) | R api | R agent |
|---|---:|---:|---:|---:|---:|---:|
| short (6–119 prov) | 47 | 2,270 | 82 | +0.044 | 0.40 | 0.59 |
| medium (122–304) | 12 | 2,305 | 36 | +0.030 | 0.61 | 0.78 |
| long (332–418) | 5 | 1,886 | 16 | +0.066 | 0.38 | 0.62 |

The agent leads in every stratum, and by the largest margin on the longest
contracts — but not monotonically, and the per-contract correlation between
contract size and the agent's AUC advantage is only **r = +0.07** across the 35
contracts with enough positives to score. The long stratum is 5 contracts and 16
positives.

The honest reading is that the agent is better across the board, and that this
data does not establish length as the reason.

## 4. Category 2 — where the agent is worse

Category 2 asks whether a provision conflicts with another elsewhere in the
contract. The agent recalls **58%** against 42% and edges ROC-AUC (0.963 vs
0.958), yet its PR-AUC is **less than half** the one-shot arm's (0.063 vs
0.139).

Those move in opposite directions because the agent flags category 2 far more
freely — 2.9% of provisions against 2.1% — and at 12 positives in 6,461 the
extra false positives cost more precision than the extra catches buy. Both arms
sit at P@0.5 = 0.04 regardless.

Neither arm produces a usable absolute probability here. The category is a
ranking signal only, and on 12 positives none of these differences should be
reported as an effect.

## 5. Cost

| | calls | input | cache-read | output | |
|---|---:|---:|---:|---:|---:|
| `llm_api` | 65 | 3,960,394 | 0 | 1,265,372 | **$51.44** |
| `agent` | 64 sessions, 791 turns | 1,168 | 31,279,423 | 1,513,156 | $106.88 API-equivalent |

The agent arm is billed to a Claude Code subscription; its dollar figure is what
the same tokens would have cost through the API, not an amount charged.

The agent costs about **2.1× more**. It spends 1.19× the output tokens — 234 per
provision against 196 — which is the more meaningful comparison, since the input
side is dominated by an agent re-sending its transcript every turn. Caching
absorbed 85% of its input; without it the 31.3M cache-read tokens would have
been billed in full.

At 80% recall, 2.1× the inference cost halves the reading. Whether that trades
well depends on what an hour of review time is worth against a dollar of
inference — for most review work it clearly does.

---

## 6. Variance has not been accounted for

**These are single runs, and every comparison above should be read with that in
mind.**

Neither arm sets a temperature or a seed, and the API exposes no way to make
sampling deterministic. A rerun will not reproduce these numbers. No systematic
repeat study was done, so the run-to-run variance of each metric is
**unquantified**, and no confidence interval, significance test or error bar in
this report accounts for it.

One incidental measurement is available and is worth stating, because it sets
the scale. The agent arm was executed twice under near-identical conditions —
same prompts, inputs, model, effort, turn ceiling and image, verified by
matching manifest hashes. Between those two executions **ROC-AUC moved by about
0.02 and recall@0.5 by about 0.10** on the full corpus. That is one observation,
not a variance estimate, but it means:

- Differences of a few hundredths in ROC-AUC are **not** interpretable on their
  own. The main-panel gap of +0.040 is around twice that scale, and the category 1
  gap of +0.049 rather more; the category 2 differences are well inside it.
- Precision and recall at a fixed 0.5 threshold are the most fragile numbers
  here. The mean predicted probability on gold positives sits close to 0.5, so a
  small shift in calibration moves many positives across the line and swings
  recall substantially. The threshold-free measures and the recall-cost curve of
  §2 are the more stable views and should be preferred when quoting a single
  figure.

What survives that caution is the recall-cost result of §2 and the equal-flag-
rate result of §1, both of which are large relative to the observed movement.
What does not survive it is any claim about category 2, about contract length, or
about small differences in ROC-AUC.

The straightforward fix is repeated paired runs — three runs of each arm over a
fixed stratified subset would bound the variance and let every gap above be
stated as inside or outside it. It has not been done, on cost.

---

## 7. Summary

- On an identical flag rate the agent arm recovers substantially more litigated
  provisions: 57% against 40% on category 1 at 3.0% flagged, 64% against 46% overall.
- It reaches 80% recall while flagging 13.6% of the contract, against 28.9% —
  less than half the reading — for about 2.1× the inference cost.
- It is **worse** on category 2 PR-AUC, flagging that category more freely than its
  precision supports. Category 2 rests on 12 positives and is a ranking signal only.
- The agent leads in every length stratum; contract length is not established as
  the mechanism.
- Run-to-run variance is unquantified. One repeat of the agent arm moved ROC-AUC
  by ~0.02 and recall by ~0.10, which is the scale against which every gap above
  should be judged.

Figures: `output/figures/exp3_llm_api_threshold_curves.png` and
`output/figures/exp3_agent_threshold_curves.png`. Numbers behind every table:
`output/exp3_comparison.json`.

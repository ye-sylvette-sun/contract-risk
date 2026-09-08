# Risk detection — results (superseded build)

One question, one method: can a model, given a contract and no access to the
opinion, rank the provisions a federal court went on to construe above the ones
it did not?

> ## ⚠ These results are superseded
>
> **Every number in this report was produced on a dataset that no longer
> exists.** Steps 1 and 2 have since been reordered so that clause boundaries
> are fixed by a call that never sees the opinion
> ([DATASET.md](DATASET.md) §3). That changed the clause segmentation, the
> positive set (201 → 226), the ids, and the evaluation set (11,636 → 11,921
> provisions). **The run has not been repeated**, and when it is, its numbers
> must not be placed beside the tables below as though they measured the same
> thing.
>
> One difference matters more than the rest: length alone now ranks provisions
> at within-contract ROC-AUC **0.705**, where this run's dataset gave 0.523.
> DATASET.md §6 explains why the old figure was the artifact and the new one is
> the property. A headline AUC has a much higher floor to clear than it did
> here.
>
> The dataset these numbers belong to is on
> `2026.9.3_legacy_multi_issue_experiment`. They are kept because a repeat needs
> something to be read against — not because they describe the current build.

> **The `llm_api` arm has been retired and deleted.** Earlier builds ran two
> arms — one stateless API call per contract against one agent session per
> contract — and this report compared them. **The agentic approach is the
> experiment.** What the two arms shared (`FIELDS`, `pred_row`, `anonymise`,
> `probs_of`) is now `runs.py`. The two-arm comparison is on
> `2026.9.1_legacy_spellbook`; the deleted arm on
> `2026.9.3_legacy_multi_issue_experiment`.

## The run

**`agent`** — one Claude Code session per contract, in a container. The model
gets a workspace (the contract, the provisions under opaque ids, three worked
examples) and decides for itself what to read and in what order.

`claude-opus-5`, effort high, ceiling 100 turns, 8 containers at a time, billed
to a Claude Code subscription. Image `contract-risk-judge:0.2.139`
(`sha256:957de41b…`), identical for all 100 sessions.

```
11,636 provisions  |  190 positive (1.6%)  |  100 contracts
100 sessions       |  1,254 turns          |  0 unjudged, 0 errored
```

Every provision of every contract was judged. No session hit the turn ceiling —
the largest, at 698 provisions, used 51 turns.

---

## 1. Headline

| panel | positives | ROC-AUC | PR-AUC | P@0.5 | R@0.5 | flagged |
|---|---:|---:|---:|---:|---:|---:|
| risky vs not | 190 | **0.899** | 0.361 | 0.35 | 0.53 | 2.5% |
| risk type 1 — intrinsic | 141 | 0.898 | 0.326 | 0.31 | 0.50 | 2.0% |
| risk type 2 — relational | 81 | 0.854 | 0.149 | 0.21 | 0.22 | 0.7% |

Bootstrap 95% CIs on ROC-AUC: risky **[0.844, 0.949]**, risk type 1
[0.831, 0.952], risk type 2 [0.752, 0.935].

At 1.6% prevalence **PR-AUC is the number to read** — ROC-AUC is flattered by
the 11,446 easy negatives. The ranking is strong, and the two risk types rank
about equally well; their intervals overlap heavily, so the 0.045 gap between
them is not an established difference.

The flag rate at threshold 0.5 (2.5%) sits close to the true prevalence (1.6%),
so the recall is not bought by flagging indiscriminately.

## 2. What a recall target costs

The practical question is not "what is precision at 0.5" but "to catch most of
what was litigated, how much of the contract must a reader read".

| recall target | threshold | precision | share flagged |
|---:|---:|---:|---:|
| 70% | 0.41 | 0.182 | 6.3% |
| 80% | 0.33 | 0.079 | 16.6% |
| 90% | 0.26 | 0.044 | 33.5% |

Catching 70% of litigated provisions costs reading **6.3%** of the contract.
Past that the curve turns sharply: 80% costs 16.6%, and 90% costs a third of the
document — at which point the ranking is no longer doing useful work.

## 3. Contract length

The agent works through a long contract in stages, so it should hold up as
documents grow.

| stratum | contracts | provisions | positives | ROC-AUC | R@0.5 |
|---|---:|---:|---:|---:|---:|
| short (9–58 prov) | 36 | 1,059 | 54 | 0.864 | 0.54 |
| medium (60–148) | 37 | 3,481 | 69 | 0.897 | 0.49 |
| long (153–698) | 27 | 7,096 | 67 | 0.876 | 0.57 |

Performance is flat across strata — the spread, 0.864 to 0.897, is well inside
the headline confidence interval. **Length neither helps nor hurts**, which is
itself the useful finding: a 698-provision agreement is ranked as well as a
20-provision letter.

## 4. The issue list

The model returns, per provision, a list of `{issue, type, prob}` entries. A
null `issue` carrying a probability is how it states "no specific defect of this
type, and here is how likely a dispute is anyway".

```
0 named issues:  5,418 (46.6%)
1 named issue:   4,831 (41.5%)
2 named issues:  1,382 (11.9%)
3+ named:            5 ( 0.0%)
```

88.1% carry no issue or exactly one, which is what the prompt asks for. Of the
1,387 multi-issue provisions, all but 5 are one type-1 entry and one type-2
entry: the model almost never names two defects of the same type.

Issue **count** alone is a strong ranker — ROC-AUC **0.838**, against 0.899 for
the probability. Gold positives carry two or more named issues 70.0% of the time
against 11.0% of negatives. The probability still carries information the count
does not, so it stays the primary score, but the count is a calibration-free
fallback that loses little.

## 5. Cost

| | sessions | turns | input | cache-create | cache-read | output |
|---|---:|---:|---:|---:|---:|---:|
| `agent` | 100 | 1,254 | 1,890 | 8,571,538 | 55,839,828 | 2,428,086 |

**$174.35 at API-equivalent rates** — what these tokens would have cost through
the API, not an amount charged; the run is billed to a Claude Code subscription.
7.6 hours of container time, about 209 output tokens per provision. Caching
absorbed 85% of the input side; without it the 55.8M cache-read tokens would
have been billed in full.

## 6. Isolation held

The container carries no `~/.claude`, no user `CLAUDE.md`, no skills, no MCP
registration and no managed policy, and only one contract's workspace is mounted
— so no session can see `dataset.csv`, the opinion, or another contract.

Across 100 sessions the confinement hook recorded **39 denied reads**, every one
an attempt at a conventional path (`/tmp/x`, `/mnt/user-data/outputs/…`,
`/contract.txt`) before the model located the real workspace. None reached a
file outside `/work`, and none was a file it could not already read inside it.

Label leakage was checked against the bytes the container received, not against
the code that writes them. The dataset's own clause ids are `pos<N>`/`neg<N>`;
provisions are presented as `c001…cNNN` in `source_span` order. Across all
workspaces: 2,048 provisions, key set exactly `{id, name, text}`, ids strictly
sequential with no gaps, and zero occurrences of `pos<N>`, `neg<N>`, `POSITIVE`,
`NEGATIVE` or `gold_*` in any workspace file, prompt or trajectory. Position
carries no signal either — a positive's mean position in `c001…cNNN` is 0.507
against 0.500 for uniform, and only 1 of 201 lands at `c001`.

## 7. What is not established

**Run-to-run variance is unquantified.** No temperature or seed is set, and the
API exposes no way to make sampling deterministic; a rerun will not reproduce
these numbers. Under the previous design, two executions of the agent arm with
matching manifest hashes moved ROC-AUC by ~0.02 and recall@0.5 by ~0.10. That is
one observation, not a variance estimate, but it is the scale against which
small differences here should be judged — the risk type 1 vs 2 gap and the
length strata all sit inside it. The threshold-free measures and §2's
recall-cost curve are the stable views.

**The gold link rests on model judgment.** Every positive carries a verbatim
passage from the opinion (median 3,761 characters, none empty), and every clause
text was located verbatim in the filed contract (`anchor_score` median 1.000).
What is *not* independently verified is that the attached passage discusses that
particular clause — the extraction model made that link, and the heuristic that
once second-guessed it was removed as too ad hoc. A human spot-check of a few
dozen positives would put a number on it; that has not been done.

**A positive is not one dispute.** (Fixed since: the rebuilt step 2 records each
defect separately, so the current dataset carries 267 issues over 202 positives
and the issue-level denominator is real.) The 201 positives trace to 138 distinct
opinion passages: 101 map to a single clause, the rest to two or more, because a
court often construes several provisions in one discussion. One passage covers 9
clauses.

**Length is not a confound here — but it was, so keep checking.** Clause length
alone ranks positives above negatives at within-contract ROC-AUC **0.523**
[0.501, 0.546], indistinguishable from chance. The previous build's figure was
**0.683**, so this is a property of the rebuilt extraction rather than a
constant of the task. The 0.899 therefore reflects substance, not a length
shortcut.

**Negatives are unlitigated, not sound.** Precision against these labels is a
lower bound.

---

## 8. Summary

- The agent ranks litigated provisions well above the rest: ROC-AUC **0.899**
  [0.844, 0.949] at 1.6% prevalence, PR-AUC 0.361.
- 70% of litigated provisions are recoverable by reading **6.3%** of the
  contract; 80% costs 16.6%.
- Performance is flat in contract length, from 9 to 698 provisions.
- Both risk types rank about equally well; the apparent gap is inside the
  confidence intervals.
- Isolation and label leakage were verified against the bytes the container
  received, not the code that writes them.
- Run-to-run variance is unquantified, and is the main caveat on every small
  difference above.

Figure: `output/figures/risk_detect_agent_threshold_curves.png`. Predictions:
`output/risk_detect_agent_preds.csv` (11,636 rows, 18 columns). Per-session logs and
trajectories: `output/llm_logs/risk_detect_agent/`.

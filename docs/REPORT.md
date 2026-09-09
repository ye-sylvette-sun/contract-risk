# Risk detection — results

What one sandboxed Claude Code session per contract finds when it is given a
contract and asked which of its provisions a court would be asked to construe,
and whether it names the defect the court actually construed.

## The run

```
model            claude-opus-5, effort high, 100-turn ceiling
harness          one container per contract, contract-risk-judge:0.2.139
                 sha256:b5f50d7dc71f6eae1ce623fbbcad0853e927ce6c564ae44d9ff53015f1bb1fec
auth             CLAUDE_CODE_OAUTH_TOKEN (subscription), never an API key
evaluation set   84 contracts, 9,890 provisions, 3 example contracts held out
gold             163 construed provisions, 225 recorded defects
cost             $169.47 at API-equivalent rates, 0 failed sessions
```

The alignment judge is `gpt-5.6-sol` at high effort — a different family from
the one being judged, and it never sees the agent's reasoning.

## 1. Ranking

Three one-vs-rest tasks. A provision's score is its probability for that type;
`risky` takes the larger of the two.

| | positives | ROC-AUC | recall ceiling |
|---|---|---|---|
| risky vs not | 163 (1.6%) | **0.868** | 0.99 |
| risk type 1 — intrinsic | 120 (1.2%) | **0.770** | 0.72 |
| risk type 2 — relational | 68 (0.7%) | **0.690** | 0.59 |
| clause length alone | | **0.691** | |

The recall ceiling is the recall available at a threshold of 0.01: the share of
gold provisions that received any entry of that type at any probability. No
threshold can reach past it.

**Risk type 2 does not beat clause length.** 0.690 against 0.691. Everything the
model contributes over the length baseline is on type 1 and on the binary
risky/not question; on the relational half it adds nothing measurable.

Figure: `output/figures/risk_detect_agent_threshold_curves.png`.

## 2. There is no usable operating point

| threshold | precision | recall | flagged |
|---|---|---|---|
| 0.3 | 0.08 | 0.66 | 1,341 (13.6%) |
| 0.5 | 0.30 | 0.16 | 88 (0.9%) |

Precision and recall cross at about t = 0.45, both near 0.25. At 1.6%
prevalence that is well above chance and still far from a threshold anyone would
operate at. What the run demonstrates is ranking, not a working detector.

The 0.99 risky ceiling has to be read with the flag rate that produces it: the
model writes at least one issue on 68% of provisions. It is not that almost
every construed provision was recognised — it is that almost every provision
received something.

## 3. Naming the right defect

The ranking task cannot tell whether a provision was flagged for the reason the
court had. The alignment check asks that separately: for each issue the agent
named, is it one of the defects recorded for that provision?

```
209 issues judged over 161 provisions
 69 aligned  =  33.0%
 69 distinct gold defects matched, of 225 in the corpus
```

**The control is the number to read this against.** Re-pairing every issue with
the defects of a *different provision of the same case* — deliberately wrong
answers, in the same contract, the same dispute, the same legal vocabulary —
still produces 28 matches:

| | main | control (same case) | ratio |
|---|---|---|---|
| all issues | 33.0% | 14.2% | 2.3× |
| gold 1.1 — lexical | 24.3% | 2.9% | **8.4×** |
| gold 2.2 — whole-contract | 35.2% | 26.4% | **1.3×** |

**On 1.1 the measurement is sound; on 2.2 it is not.** A relational defect is
described as "this provision has to be read against that one", and in the
control the foil often *is* a provision of the same instrument, so the
descriptions are near-interchangeable. The judge cannot separate them.

This is the same conclusion the ranking reaches by a completely independent
route: on risk type 2, AUC equals the length baseline. Two measurements that
share no machinery agree that the relational half of the taxonomy carries no
usable signal in this setup.

**Risk type is not part of the alignment test.** The judge is shown neither
side's type and candidates are not filtered by it, because the dataset's type
labels come from the case's Westlaw key rather than from the passage. Type
agreement is reported separately: of the 69 matched pairs, 52 (75.4%) agree on
the coarse type. The other 17 name the same defect and classify it differently.

Figure: `output/figures/issue_alignment_threshold_curves.png`.

## 4. What the two measurements say together

```
risky recall ceiling   0.99      almost every construed provision got an entry
alignment              33.0%     about a third of those entries name the defect
```

The model is good at deciding *where* to look and much weaker at saying *what is
wrong*. Reading the misses confirms it: the defects it names are usually real
drafting problems in the right provision — an undefined term, a missing cure
period, a cross-reference to a mis-dated letter — but not the one that became a
lawsuit.

The judge is not being strict about this. Its scores are effectively binary:
of 209 issues, none scored between 0.01 and 0.49, so lowering the threshold
changes nothing. Of the 140 misses, 15 quote language that also appears in a
recorded defect, and reading them, about 4 are genuinely arguable. Counting all
15 as hits would move 33.0% to 39.7% and the control from 14.2% to 18.8% — the
ratio falls from 2.3× to 2.1×, so a looser standard buys score and loses
discrimination.

## 5. What is not established

- **No noise floor.** Every number here is one observation. The same prompt has
  never been run twice on this dataset, so no difference between configurations
  can be called real.
- **The type-2 conclusion is about this setup, not about the task.** AUC 0.690
  against a 0.691 baseline says this pipeline extracts nothing; it does not
  say a relational defect is undetectable.
- **Recall is a lower bound.** Step 2 records what it can locate in the
  opinion, and 45 issues in the corpus need a document the corpus does not
  hold. A defect the court construed but the dataset never recorded counts
  against the agent as a miss.
- **The alignment judge's absolute rate is not meaningful on its own** — only
  the gap to its control is, and on 2.2 that gap is small.
- **The 75.4% type agreement is agreement with the dataset's labelling
  convention, not accuracy.** The dataset's per-defect type comes from the
  case's Westlaw key, which is a case-level fact.

## 6. Cost

```
detection      $169.47   84 sessions, claude-opus-5, effort high
alignment      209 + 197 calls, gpt-5.6-sol, effort high
dataset build  step 2 over 62 cases, step 3 over 55, gpt-5.6-sol
```

# Risk detection — results

One question, one method: given a contract and no access to the opinion, can a
model rank the provisions a federal court went on to construe above the ones it
did not?

## The run

**`agent`** — one Claude Code session per contract, in a container. The model
gets a workspace (the contract, the provisions under opaque ids, the case's
sibling documents where it has any, and the worked examples) and decides for
itself what to read and in what order.

`claude-opus-5`, effort high, ceiling 100 turns, 4 containers at a time, billed
to a Claude Code subscription. Image `contract-risk-judge:0.2.139`
(`sha256:b5f50d7d…`), identical for every session. Commit `4b5e34a`, clean tree.

```
5,505 provisions  |  121 positive (2.20%)  |  50 contracts
50 sessions       |  0 unjudged, 0 errored |  $104.52 at API-equivalent rates
```

**The sample is stratified, not random.** 25 of the 50 contracts carry a gold
risk type 2 and 25 do not, drawn in two batches to get enough relational
defects to say anything about them. Prevalence here (2.20%) is therefore higher
than the corpus (1.61%), and **no rate in this report is an estimate of the
corpus**. Comparisons within the run, and against the length baseline computed
on the same rows, are unaffected.

---

## 1. Headline

| panel | positives | ROC-AUC | P@0.5 | R@0.5 | flagged |
|---|---:|---:|---:|---:|---:|
| risky vs not | 121 | **0.827** | 0.53 | 0.26 | 1.1% |
| risk type 1 — intrinsic | 87 | **0.792** | 0.45 | 0.25 | 0.9% |
| risk type 2 — relational | 51 | **0.712** | 0.71 | 0.20 | 0.3% |

Clause length alone ranks these rows at **0.731**. That is the floor every
panel has to clear, and risk type 2 clears it by less than the run-to-run
variance measured in §4.

The court's defect was on a provision the model flagged at all in **97%** of
distinct risk type 1 defects and **95%** of type 2. Detection is not where this
loses.

---

## 2. What a recall target costs

| recall | threshold | precision | flagged |
|---:|---:|---:|---:|
| 0.78 | 0.24 | 0.062 | 27.6% |
| 0.80 | 0.22 | 0.053 | 33.3% |
| 0.94 | 0.16 | 0.039 | 53.1% |

At 2.2% prevalence, flagging 27.6% of a contract to catch 78% of what a court
construed is a 2.8× lift on reading it in order. The flag rate is the column
that keeps the other two honest.

---

## 3. Risk type, two ways

The output is a **list**, so "was the type right" has two honest readings, and
both are reported over **distinct** gold defects — deduplicated on the defect
text, because one recorded defect can sit on several provisions of a contract
and counting it five times would weight the score by how often a drafter copied
a paragraph.

| | distinct defects | `wins` | `named` |
|---|---:|---:|---:|
| risk type 1 | 70 | 56 (**80%**) | 58 (83%) |
| risk type 2 | 44 | 20 (**45%**) | 27 (**61%**) |

`wins` asks whether the gold type outscored the other. `named` asks only whether
the gold type was listed at all, wherever it ranked. **The gap is how much of
the type error is a ranking difference rather than a missing judgement** — 3
points on type 1, **16 on type 2**. A third of the type-2 failures are defects
the model did name relationally, at a probability below something else it found
in the same provision.

`named` is an upper bound on finding the court's defect, not a measure of it: it
credits any issue of the right type on the right provision, whatever that issue
describes. `issue_alignment_check.py` answers the stricter question and has not
been run on this build.

### By what a reader must be able to open

`scope` comes from step 3 ([DATASET.md](DATASET.md)): whether the defect is
visible in the clause alone, needs the rest of the contract, or needs a sibling
document filed in the same case.

| | defects | `named` | `wins` |
|---|---:|---:|---:|
| **type 1** clause | 48 | 85% | 83% |
| **type 1** contract | 16 | 81% | 81% |
| **type 1** case | 6 | 67% | 50% |
| **type 2** clause | 6 | 33% | 33% |
| **type 2** contract | 28 | 68% | 50% |
| **type 2** case | 10 | 60% | 40% |

Type 1 holds above 80% wherever the defect is reachable without leaving the
instrument. The two `case` rows are 6 and 10 defects and carry no weight on
their own.

---

## 4. Run-to-run variance — the noise floor

**This is the most consequential number in the report, and it bounds every
comparison anyone will want to make.**

One configuration was run twice, unchanged, over the same 4 contracts:

| | provisions whose type changed | mean \|Δp\| per provision |
|---|---:|---:|
| same prompt, two runs | **101 / 650 = 15.5%** | **0.080** |
| across a prompt change | 78 / 650 = 12.0% | 0.081 |

**Two runs of one prompt differ more than two prompts do.** Smaller cells move
correspondingly: over the eight distinct type-1 defects in those contracts the
same prompt scored 3/8 and then 1/8; over six type-2 defects, 5/6 and then 4/6.

Where the instability comes from is visible in the issue text. On provisions
whose type flipped between the two runs, the median textual similarity between
what the model named the first time and the second was **0.12**; on provisions
that held, **0.20**. The model is not re-typing a stable finding — **it is
naming a different defect**. The type follows the finding, so the type churns.
(Sequence similarity on legal prose is a crude instrument; the direction is
sound, the magnitudes are not.)

**What follows from this.** A long provision often carries several real defects;
the court construed one of them; the model reports whichever it noticed this
time. That is why detection is at 95–97% while the type label sits at 45–80%,
and why three successive prompt revisions aimed at the type boundary each moved
the metric by less than this floor. **No single-run difference in ROC-AUC or in
a 40-defect cell should be read as an effect.** Establishing error bars — the
same configuration run three times — is the precondition for the next
comparison, not an optional refinement.

---

## 5. The issue list

```
0.78 issues per provision   |   24% empty   |   2+ entries 1.80%
both types on one provision 1.29%   |   any type-2 entry 32.3%
scored ≥0.5 on 1.05% of provisions   |   highest probability reached 0.86
```

The ≥0.5 rate is the one the prompt anchors ("on the order of one or two in a
hundred"), and against the corpus gold rate of 1.61% it is close. Against this
stratified sample's 2.20% it looks low; that is the stratification, not the
model.

Worked examples were widened from three, standing on two distinct provisions, to
**every construed provision of the two held-out contracts with every defect the
court found in it** — six provisions, ten defects, two of them carrying defects
of both types. Provisions given both types went 0.65% → 1.29%, against a
same-prompt spread of 0.15 points on the replication above. It is the only
prompt change in this line of work whose effect measurably exceeded the noise
floor, and it cost nothing: those contracts had already left the evaluation set.

---

## 6. What is not established

- **No error bars.** §4 measures the floor on one pair of runs over 4 contracts.
  Every panel figure here is a single draw.
- **The sample is stratified**, so prevalence-dependent quantities (precision,
  PR-AUC) do not transfer to the corpus.
- **Whether the model found the court's defect** — only that it flagged the
  provision and named something of the right type. `issue_alignment_check.py` is
  the instrument for this and has not been run.
- **Risk type 2 at 0.712 against a 0.731 length baseline** is not established to
  beat length at all.
- **Part of the type disagreement is irreducible.** Gold types were assigned by
  a step that read the opinion, and so record how the court framed the dispute;
  the agent reads only the contract. Where a defect can honestly be described as
  intrinsic or as relational, the framing is not recoverable from the model's
  inputs.
- **No comparison to a non-agentic baseline.** The second arm was retired; see
  [EXPERIMENTS.md](EXPERIMENTS.md) §8.

---

## 7. Summary

Detection works: 97% and 95% of the defects a court construed sat on a provision
the model flagged, and the risky panel ranks at 0.827 against a 0.731 floor.
Typing them is where the method stands or falls, and it is limited by something
prompt wording has not moved — the model names one of a provision's several real
defects, and which one is not stable across runs of the identical prompt.

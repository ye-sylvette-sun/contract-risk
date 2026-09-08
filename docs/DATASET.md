# The dataset

Contract clauses labelled **risky** or **not risky**, where *risky* means a
United States federal court construed the clause in a written opinion. Every
clause is verbatim text cut out of the scanned filing it was attached to.

```
12,036 rows  |  202 positive / 11,834 negative  (1.7% positive)
62 cases     |  103 contracts                   |  14.1 MB
267 issues   |  the defects the courts construed, named one by one
```

A positive carries a list of **issues** — one per distinct defect the court
construed in it — so the corpus holds 267 defects over 202 clauses: 146 clauses
with one, 50 with two, and 6 with three or more. That list is the denominator
for scoring a model at the issue level, which an earlier build could not supply.

**Every issue here is one a reader holding the contract could reach.** Step 3
asks what someone would have to read to see each defect, and the build drops the
43 that turn on material the corpus does not hold — an email, a course of
dealing, an exhibit never filed — along with the 24 positives left with none.

Positives carry 267 risk-type codes between them — 1.1 × 187, 1.3 × 10,
2.2 × 70 — because 25 of the 202 carry more than one. Where the case's Westlaw
keys give exactly one candidate code the label is `taxonomy_provenance =
westlaw` (119 clauses) and the model had no choice; where the case has several,
the model chose among them and the provenance is `model` (83 clauses).

---

## 1. What a label means

**POSITIVE** — the opinion shows the parties fought over this clause and the
court discussed it. Reaching litigation is what makes a clause risky, so a clause
the court examined and *upheld* counts exactly as much as one it struck down. How
the case came out plays no part in the label.

**NEGATIVE** — every other clause of every contract filed in the **same case**.

Because a positive's own contract also supplies negatives, the two classes share
the drafter, the domain, the era and the OCR condition; the only systematic
difference between them is the one being labelled. The other agreements of a case
supply negatives too — a case commonly files several instruments and the court
reaches only some. 28 of the 103 contracts contain no positive at all, and are
the only documents here where the right answer is "nothing to flag".

An unlitigated clause is **lower risk, not sound**. It may be well drafted, or it
may carry a defect nobody had occasion to fight over. Precision measured against
these labels is a lower bound.

**No model assigns the label.** It comes from the **Westlaw Key Numbers** the
case was filed under. Twelve keys map onto six risk-type codes:

| code | risk type |
|---|---|
| 1.1 | Lexical ambiguity — a word or phrase is open to more than one reasonable reading |
| 1.2 | Mechanical error — a mistake in writing, grammar, spelling or punctuation |
| 1.3 | General-vs-specific / list scope — a catch-all sits against enumerated specifics |
| 2.1 | Conflicting clauses — this clause cannot be squared with another |
| 2.2 | Whole-contract coherence — the meaning only emerges when the contract is read whole |
| 2.3 | Recitals vs operative text |

A model is *handed* the codes its case was selected under and may only choose
among them; anything else is rejected. Every row carries the keys it was selected
under, so a label traces back to the headnotes.

### Which code, when a case carries several

A case is filed under one or more Westlaw keys, and those keys need not map to
one code. The **binary** label never depends on this — a positive is a positive
because the opinion shows the court construed the clause, whatever the keys say.
Only the risk *type* does.

- **One code.** Nothing to choose: the code is a Westlaw fact and no model
  touches it.
- **Several codes.** The model is shown the whole six-code taxonomy as
  background and the case's own codes as the **candidates**, and says which of
  them the dispute over *this* clause turned on. It may name more than one — a
  court can find a phrase ambiguous on its face *and* resolve it by reading the
  instrument as a whole. Anything outside the candidate set is rejected.

`taxonomy_provenance` records which of the two produced a row — `westlaw` or
`model` — so a consumer who wants the stricter dataset can keep only the rows no
model had a say in. `taxonomy` is therefore a comma-separated list, and a
positive can be both risk type 1 and risk type 2.

An earlier build dropped every multi-code case instead, at 24 cases and 42
contracts, to protect a property only the type column has.

---

## 2. Core principle: locate, don't transcribe

**No model ever writes dataset text.** A model's answer is a *pointer*: a coarse
line window, plus the clause's first 8 words and last 8 words copied off the
scan.

`lib.locate()` then does the work — it widens the window, matches the anchors
against the raw file, **snaps the boundary to where the anchors actually are**,
and slices the characters out of the file. An anchor scoring below 0.75 means the
model pointed at the wrong place, and the clause is rejected with its reason
recorded.

This buys three things at once: the anchors *prove* the range is real, they
*repair* an off-by-a-few line count, and they cut **inside** a line, which is how
two lettered sub-clauses that OCR ran together are told apart.

`replay_anchors.py` re-runs every stored answer through `locate()` at no API
cost, which is how the thresholds were tuned and how a rebuild is checked.
Against the current logs: **12,076 of 12,154 anchors match (99.4%)**, 97.6% of
them exactly, and 3.6% of boundaries were snapped. Only step 1's logs are
scored — step 2 answers in ids and reports no spans.

The span is then normalised — page furniture dropped, hyphen-split words
rejoined, whitespace collapsed. Nothing else. **No spelling is corrected, no
number repaired, no missing word restored.** Where the scan reads `givr. thr.
rlr.filulting pilrty`, that is what the dataset carries. Models under evaluation
should meet the documents as they exist.

---

## 3. The steps

### Step 0 — build the corpus (no LLM)

Join the Westlaw headnotes, the opinion text and the docket linking sheet; keep
the cases in scope; register each contract to `output/contracts/`. The filings
arrive OCR'd (`ocrmypdf --force-ocr`) and sliced to each named agreement's own
lines — a verbatim line-range cut. An 8-word-shingle containment check at 0.90
catches the same document filed twice under two names.

### Step 0b — reject two-column scans (`gpt-5.6-terra`, low effort, one call per contract)

OCR reads a two-column page straight across, so every output line splices the
left column onto the right, from two passages with nothing to do with each other.
Nothing downstream can see this: the anchors match, the line range is real, and
the "clause" is alternating halves of two different clauses.

The document is sampled as windows spread from first line to last, and the model
must **quote the interleaved lines back verbatim, with their line numbers** — so
every rejection can be checked against the file rather than taken on trust.

It rejects 14 of 117 contracts, mostly insurance policies with two-column
endorsement pages. Nothing is deleted: `output/layout.json` keeps the verdict and
the quoted lines, and step 1 skips the contract.

### From the linking sheet to the corpus

Each filter with the count it leaves. Everything here is recomputable from
`data/`, `contract_risk/generated/` and `output/{cases,contracts,layout}.json`.

| | agreements | cases |
|---|---:|---:|
| in the docket linking sheet | 9,055 | 3,596 |
| with a Bloomberg **Entry Document** link — the rest cite an exhibit nobody can download | **1,374** | 732 |
| downloaded, OCR'd and line-cut by Contract-Risk — *a sample of the above, not a filter* | **209** | 120 |
| in a case in scope: filed under one of the 12 keys, with opinion text on disk | 143 | 81 |
| Contract-Risk's verdict is `usable` | 123 | 68 |
| registered — de-duplicated at 0.90 shingle containment, over the length floor | **117** | 68 |
| single-column — Step 0b rejects 14 two-column scans | **103** | 62 |

Every filter here is about whether a document is *usable*; none is about labels.
An earlier build also required a case's Westlaw keys to map to a single taxonomy
code, which cost 42 contracts to buy a property `taxonomy_provenance` now
records per row for free.

### Step 1 — every clause of a contract (`gpt-5.6-sol`, high effort, one call per contract)

Enumerates a contract's clauses in document order. **This step, and only this
step, decides where a clause starts and ends**, for both classes.

It is deliberately first and deliberately blind: one contract, no opinion, no
Westlaw key, no hint that the document was ever litigated. A clause boundary is
a fact about the drafting, and nothing about which clause a court happened to
construe may move it. Every clause gets an id — `c001`, `c002`, … in document
order — and step 2 can answer only in those ids.

This ordering is a correction. When the dispute step ran first it drew its own
spans, and they drifted with what the court had said — fragmenting a provision so
each piece could carry a defect, or widening one to fit two. That drift is a
difference between positives and negatives with nothing to do with drafting risk,
and a classifier can learn it.

It runs on **every** registered single-column contract of a case in scope, so no
positive lacks negatives from its own document and the agreements the court
never reached are inventoried too.

Clauses come back sorted by span, so the artifact does not depend on the order
the model happened to list them in. Two over-capture detectors then run as
**flags** — overlapping spans, and a clause longer than 10× the contract's
median. They are printed and stored; neither rejects anything. 35 of 103
contracts carry at least one.

### Step 2 — which of those clauses were disputed, and over what (`gpt-5.6-sol`, high effort, one call per case)

The call carries the **numbered opinion, every registered contract of the case
and step 1's clause list for each**, plus the risk codes the case was selected
under and the headnote text. The model names the clauses the parties disputed —
**by id** — and for each one a list of issues.

An issue is one distinct defect the court construed, carrying its own risk type
and its own verbatim opinion passage. Two entries mean two different problems; a
defect the court returns to later in the opinion is still one issue. A clause
whose wording is argued to be ambiguous *and* to conflict with another section
is two issues, not one clause with two codes.

**It cannot report a span.** The schema has no line range and no anchors, and an
id absent from that contract's list is rejected. So the verbatim guarantee is
unchanged — `build_dataset.py` still re-cuts every row from the file — but the
boundary belongs to the step that never saw the opinion.

**Returning no clauses is a valid answer**, and happens when the disputed
agreement was never filed: 6 of 62 cases return none. Where the court construed
language that no listed clause contains, it goes to `unlocated` — a record of
the miss rather than a nearby clause reported in its place. There are 43 of
these across 21 cases; §6 breaks them down.

### Step 3 — what each issue needs in order to be seen (`gpt-5.6-sol`, high effort, one call per case)

Step 2 was shown **every contract of the case**, while the experiment hands its
model one. So an issue can be recorded that no reader of the target contract
could ever find, and nothing in the data says which. This step says which.

It carries the full numbered opinion, every contract of the case, and each issue
step 2 recorded, and answers per issue **what a reader would have to hold**:

| scope | | in the dataset |
|---|---|---|
| `clause` | the quoted clause alone states the defect | 133 |
| `contract` | other provisions of the same contract are needed | 95 |
| `case` | another document filed in the case, named in `scope_needs` | 39 |
| `external` | case-specific material the corpus does not hold | *dropped* |

**General knowledge is not `external`** — the law, terms of art and ordinary
usage sit on the reader's side. The test is whether the missing thing is
*particular to this case* and absent from the filed documents.

**It changes no label.** `disputes.json` is read and not written; the step only
annotates, and `build_dataset.py` acts on the annotation.

Test-retest agreement across three runs is 90–93% on the four-way scope and 97%
on reachability. Like the clause-to-passage link (§6) it is a model judgement,
mechanically unverified.

### `build_dataset.py` (no LLM)

One row per clause step 1 enumerated. A row is POSITIVE when step 2 named its
id, NEGATIVE otherwise — a set-membership test on ids, nothing more. There is no
longer any line arithmetic reconciling two models' ideas of where a clause ends,
because there is only one.

It refuses to write unless the data survives four checks:

- every row is **re-cut from disk** at its recorded span and must reproduce
  `clause_text` exactly;
- no two rows occupy the same span of the same contract;
- no text carries both labels;
- every positive has at least one issue and a non-empty passage.

A clause that reproduces a positive's text character for character somewhere
else is **dropped, not labelled** — it carries whatever made that positive
risky. This is boilerplate repeated across endorsements, and cases filing
several editions of one instrument.

An issue step 3 scoped `external` is dropped for the same reason in the other
direction: no harness can put it within a contract reader's reach, so scoring
against it measures the record rather than the reader. A positive left with no
issue goes with it — **dropped, not relabelled**, because the court did construe
that clause and calling it NEGATIVE would assert the opposite. That removes 43
issues and 24 positives, and each surviving row's `opinion_comment` is rebuilt
from the issues that remain.

A negative in a contract holding no positive of its own carries the **case's**
taxonomy code — still a Westlaw key, and unambiguous because every case in scope
is filed under exactly one.

---

## 4. Columns

| column | |
|---|---|
| `citation`, `key` | the case, and the Westlaw keys it was selected under |
| `taxonomy` | the risk type code(s), comma-separated — a clause can carry both 1.x and 2.x |
| `taxonomy_provenance` | `westlaw` (the case has one code; no model chose it) or `model` (the case has several and a model said which apply) |
| `clause_id` | `c001…` **within the contract**, in document order — assigned by step 1, before anything knew the label. Unique per `(contract_id, clause_id)` |
| `clause_name`, `label` | `POSITIVE` / `NEGATIVE` |
| `provenance` | which step produced the row |
| `case_desc` | one line on the dispute |
| `contract_id`, `contract_file` | which document it was cut from |
| `context_contract_ids` | the other contracts filed in the same case, comma-separated — what step 2 was shown, and what a reader needs mounted to reach a `case`-scoped issue. Empty for the 4,074 rows whose case holds one document |
| `source_lines`, `source_span` | the line range and character offsets the anchors snapped to |
| `clause_text` | the normalised extraction — **the dataset text** |
| `anchor_score` | the match quality |
| `n_issues` | how many distinct defects the court construed (0 for a negative) |
| `issues` | JSON list, one per defect: `risk_type`, `issue` (one sentence naming it), `opinion_lines`, `opinion_comment`, and step 3's `scope` (`clause` / `contract` / `case`) with `scope_needs` naming the document a `case` issue turns on |
| `opinion_comment` | the issue passages joined, deduplicated — "what the court said about this clause" without reassembling `issues` |

`clause_id` deliberately encodes document position and nothing else. The earlier
`pos1`/`neg1` scheme put the gold label in the identifier, and an experiment
that showed a model the raw ids handed it the answer; that class of mistake is
now impossible at the source rather than patched at the experiment.

`source_span` and `anchor_score` together make every row reproducible from the
contract file alone, without any model output.

---

## 5. Cost

Measured over the current build, from `output/llm_logs/`:

| step | model | calls | input | output |
|---|---|---:|---:|---:|
| 0b layout | `gpt-5.6-terra` | 117 | 1,065,625 | 14,719 |
| 1 inventory | `gpt-5.6-sol` | 103 (one per contract) | 2,570,290 | 1,504,000 |
| 2 disputes | `gpt-5.6-sol` | 62 (one per case) | 3,630,000 | 190,000 |
| 3 issue scope | `gpt-5.6-sol` | 56 (one per case with issues) | 2,714,000 | 72,000 |
| **total** | | **338** | **9,979,915** | **1,780,719** |

Dollar cost depends on the provider's rates at the time and is not fixed here.
Step 1 dominates the output side: it locates every clause of every contract,
where steps 2 and 3 return only ids and short verdicts. Steps 2 and 3 dominate
the input side, and for the same reason — both carry the opinion and every
contract of the case, step 2 adding step 1's clause lists and step 3 the issues
step 2 produced.

Step 0 and `build_dataset.py` make no model calls, so the dataset rebuilds from
the stored artifacts without an API key. Every call's full prompt, response and
token usage is kept under `output/llm_logs/`.

---

## 6. Known limits

- **Clause length separates the classes, and this is the headline caveat.**
  Positives run to a median 588 characters against the negatives' 332, and
  length alone ranks them at within-contract ROC-AUC **0.705**. Any model's AUC
  must be read against that baseline.

  An earlier build reported 0.523, but its positives were cut by a model that
  had read the opinion, so their spans hugged the disputed language rather than
  the clause containing it — a label-dependent boundary, and a worse problem
  than the confound it hid. Over-capture does not explain the current figure
  either: the AUC is *higher* among contracts step 1 did not flag for it (0.749)
  than among those it did (0.665). Litigated language really does sit in longer
  clauses.
- **A positive is not one dispute, but its issues are counted.** The 267 issues
  trace to 158 distinct opinion passages: a court often construes several
  provisions in one discussion, so a passage can serve more than one clause. What
  is now separate is the *defect* — each issue names its own, so "how many
  problems did the court find here" is answerable where it previously was not.
- **43 of the 310 issues needed material the corpus does not hold**, and were
  dropped. Most are **not missing documents**: 26 turn on evidence of what the
  parties did — spoofed emails, a course of dealing — and 17 on a document the
  record lacks. A court decides construction disputes on more than the
  instrument, and a corpus of instruments can never hold all of it.
- **39 surviving issues need a sibling document**, and 36 positives depend on
  one. `context_contract_ids` names what to mount; an experiment that gives a
  model one contract cannot reach them.
- **The scope annotation is a model's judgment**, like the clause-to-passage
  link below: 90–93% test-retest agreement, no human check.
- **The clause-to-passage link is the model's judgment, not a verified fact.**
  What *is* verified mechanically: the clause text was located verbatim in the
  filed contract (`anchor_score` mean 0.997, 11,749 of 12,036 exact), and the
  taxonomy is a subset of the case's Westlaw key codes. What is not: that the
  attached passage actually discusses that clause. Nothing second-guesses the
  model on that point — the heuristic that once did was removed as too ad hoc. A
  human spot-check of a few dozen positives would put a number on it; it has not
  been done.
- **43 things the courts construed are not in the dataset**, recorded in
  `disputes.json` as `unlocated` across 21 of 62 cases. 39 are documents the
  corpus does not hold; 4 are text step 1 did not enumerate. **None became a
  mislabelled negative** — all 4 fall outside every enumerated span, so they are
  absent rows, not wrong ones. The effect is on recall, not label integrity.
  These are a *different* 43 from step 3's `external` issues, and the shared
  number is coincidence: `unlocated` is what step 2 could not attach to any
  clause, `external` an issue it did attach and step 3 found unreachable.
- **Over-capture between two correct anchors is undetectable.** A range that
  starts and ends at the right clause but swallows an intervening one passes
  every check. 35 contracts carry a flag for it; the flags reject nothing, and
  the longest negative (18,970 characters) is the clearest candidate.
- **OCR sometimes duplicates text across a page boundary** and the span swallows
  both copies. The copies differ (`investment finds` vs `investment funds`), so
  no text rule finds them safely and none is applied.
- **`clause_name` is free text and is never verified** against what the anchors
  located.
- **The opinion passage is range-only** — cut from reported line numbers with no
  anchor. It is context, not label-bearing text.
- **Clause selection is not reproducible.** One sample per case; a second sample
  would not return an identical list.
- **A contract split across two OCR files** is registered as two documents, and a
  clause spanning the split cannot be located.

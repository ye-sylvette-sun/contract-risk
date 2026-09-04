# The dataset

Contract clauses labelled **risky** or **not risky**, where *risky* means a
United States federal court construed the clause in a written opinion. Every
clause is verbatim text cut out of the scanned filing it was attached to.

```
11,798 rows  |  201 positive / 11,597 negative  (1.7% positive)
62 cases     |  103 contracts                   |  12.4 MB
```

Positives carry 236 risk-type codes between them — 1.1 × 134, 1.3 × 17,
2.2 × 85 — because 35 of the 201 carry more than one. Where the case's Westlaw
keys give exactly one candidate code the label is `taxonomy_provenance =
westlaw` (111 clauses) and the model had no choice; where the case has several,
the model chose among them and the provenance is `model` (90 clauses).

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
reaches only some. 27 of the 103 contracts contain no positive at all, and are
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

An earlier build dropped every multi-code case instead. That cost 24 cases and
42 already-registered contracts — about 63% more rows — to protect a property
only the type column has, and the binary label never lacked.

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

The document is sampled as windows spread from its first line to its last — the
signature is a break recurring line after line, and a single window at the front
would see only front matter — and the model must **quote the interleaved lines
back verbatim, with their line numbers**. That requirement is what makes the
verdict checkable: every rejection can be looked up in the file rather than
taken on trust.

It rejects 14 of 117 contracts, most of them insurance policies with two-column
endorsement pages. Nothing is deleted — `output/layout.json` keeps the verdict,
the model's finding and the lines it quoted, and step 1 skips the contract.

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

Every filter here is about whether a document is *usable*. There is no longer a
filter about labels.

The previous build added one — a case was kept only if all its Westlaw keys
mapped to a single taxonomy code — and it took 109 contracts down to 67 over 39
cases. It bought one property: that the risk type was a Westlaw fact with no
model in the loop. It is now bought per row instead, by
`taxonomy_provenance`, which costs nothing and keeps the other 42 contracts.

### Step 1 — which clauses were disputed (`gpt-5.6-sol`, high effort, one call per case)

The call carries the **numbered opinion plus every registered contract of the
case**, the risk codes the case was selected under, and the headnote text. The
model returns which clauses the parties disputed, where each sits, and — for each
one — the passage of the opinion showing the dispute. That last requirement keeps
clause selection tied to the court's own words rather than to what looks risky.

**Returning no clauses is a valid answer**, and happens when the disputed
agreement was never filed.

### Step 2 — every clause of a contract (`gpt-5.6-sol`, high effort, one call per contract)

Enumerates a contract's clauses in document order, producing the negatives. It
runs on every contract step 1 was shown, so no positive lacks negatives from its
own document and the agreements the court never reached are inventoried too.

The model is asked for **every** clause, not for the ones step 1 left over. It
is given the contract and nothing else — no opinion, and no indication of which
clause the court construed — so its list normally contains the positive as well.
The overlap is removed afterwards by `build_dataset.py`, on line spans alone.
Withholding step 1's answer is the point: were the model told which clause was
litigated, its enumeration of the others could be shaped by that, and the two
classes would differ by more than the one property being labelled.

Clauses come back in document order, sorted by span, so the artifact does not
depend on the order the model happened to list them in. Two over-capture
detectors then run as **flags** — overlapping spans, and a clause longer than
10× the contract's median. They are printed and stored; neither rejects
anything.

A third detector used to check whether the model reported its clauses out of
order. The sort made it vacuous — it compared the sorted list against itself —
and it was removed rather than left reporting a constant.

### `build_dataset.py` (no LLM)

Assembles the rows and refuses to write unless the data survives four checks:

- every row is **re-cut from disk** at its recorded span and must reproduce
  `clause_text` exactly;
- no two rows occupy the same span of the same contract;
- no text carries both labels;
- a negative whose lines meet a positive's is dropped, not labelled.

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
| `clause_id` | `pos1…` / `neg1…` **within the contract**, in document order — unique per `(contract_id, clause_id)` |
| `clause_name`, `label` | `POSITIVE` / `NEGATIVE` |
| `provenance` | which step produced the row |
| `case_desc` | one line on the dispute |
| `contract_id`, `contract_file` | which document it was cut from |
| `source_lines`, `source_span` | the line range and character offsets the anchors snapped to |
| `clause_text` | the normalised extraction — **the dataset text** |
| `anchor_score` | the match quality |
| `opinion_comment` | the passage showing the dispute (positives only) |

`source_span` and `anchor_score` together make every row reproducible from the
contract file alone, without any model output.

---

## 5. Cost

Measured over the current build, from `output/llm_logs/`:

| step | model | calls | input | output |
|---|---|---:|---:|---:|
| 0b layout | `gpt-5.6-terra` | 117 | 1,065,625 | 14,719 |
| 1 extract | `gpt-5.6-sol` | 62 (one per case) | 3,406,100 | 239,898 |
| 2 inventory | `gpt-5.6-sol` | 103 (one per contract) | 2,570,290 | 1,576,530 |
| **total** | | **282** | **7,042,015** | **1,831,147** |

Dollar cost depends on the provider's rates at the time and is not fixed here.
Step 2 dominates the output side: it transcribes every clause of every contract,
where step 1 returns only the disputed ones.

Step 0 and `build_dataset.py` make no model calls, so the dataset rebuilds from
the stored artifacts without an API key. Every call's full prompt, response and
token usage is kept under `output/llm_logs/`.

---

## 6. Known limits

- **Clause length no longer separates the classes — verify that it still
  doesn't.** Positives and negatives run to almost the same length (median 329
  characters against 331), and length alone ranks them at within-contract ROC-AUC
  **0.523** [0.501, 0.546], indistinguishable from chance. The previous build was
  **0.683**, a real shortcut, and the difference is the rebuilt step 1 cutting
  tighter spans. Because it is a property of the extraction rather than of the
  task, re-measure it after any rebuild before quoting a model's AUC.
- **A positive is not one dispute.** Every positive carries a verbatim passage
  from the opinion — none is empty, the median is 3,761 characters — but the 201
  positives trace to only **138 distinct passages**. 101 map to a single clause;
  the rest map to two or more, because a court often construes several
  provisions in one discussion, and one passage covers 9. Treat a positive as
  "this clause was part of a litigated dispute", not "this clause had its own
  dispute".
- **The clause-to-passage link is the model's judgment, not a verified fact.**
  What *is* verified mechanically: the clause text was located verbatim in the
  filed contract (`anchor_score` median 1.000), and the taxonomy is a subset of
  the case's Westlaw key codes. What is not: that the attached passage actually
  discusses that clause. Nothing second-guesses the extraction model on that
  point — the heuristic that once did was removed as too ad hoc. A human
  spot-check of a few dozen positives would put a number on it; it has not been
  done.
- **Over-capture between two correct anchors is undetectable.** A range that
  starts and ends at the right clause but swallows an intervening one passes
  every check. 22 contracts carry a flag for it; the flags reject nothing, and
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

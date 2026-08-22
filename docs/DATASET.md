# The dataset

Contract clauses labelled **risky** or **not risky**, where *risky* means a
United States federal court construed the clause in a written opinion. Every
clause is verbatim text cut out of the scanned filing it was attached to.

```
6,835 rows  |  144 positive / 6,691 negative  (2.1% positive)
39 cases    |  67 contracts                   |  8.9 MB
```

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
reaches only some. 16 of the 67 contracts contain no positive at all, and are the
only documents here where the right answer is "nothing to flag".

An unlitigated clause is **lower risk, not sound**. It may be well drafted, or it
may carry a defect nobody had occasion to fight over. Precision measured against
these labels is a lower bound.

**No model assigns the label.** It comes from the **Westlaw Key Numbers** the
case was filed under. Twelve keys map onto six risk-category codes:

| code | risk category |
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

### Step 0b — reject two-column scans (Sonnet 5, low effort, one call per contract)

OCR reads a two-column page straight across, so every output line splices the
left column onto the right, from two passages with nothing to do with each other.
Nothing downstream can see this: the anchors match, the line range is real, and
the "clause" is alternating halves of two different clauses.

Two independent detectors run and **either one rejects**:

- **A gutter score** — a run of blank space at the same column position on many
  *consecutive* lines. Persistence is the discriminator: a body column holds its
  gutter for dozens of lines, a letterhead for five. Costs nothing.
- **A model call** — the document is sampled as windows spread from first line to
  last, and the model must **quote the interleaved lines back verbatim**, so
  every rejection can be looked up in the file.

They fail differently. The gutter is blind to a document whose left column is
often empty; the model is blind to a two-column passage occupying a small
fraction of a long filing. Between them they reject 8 of 117 contracts, four
being insurance policies with two-column endorsement pages. Nothing is deleted —
`output/layout.json` records both scores and the evidence.

### From the linking sheet to 67 contracts

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
| single-column — Step 0b rejects 8 two-column scans | **109** | 68 |
| in a case filed under a **single risk category** | **67** | **39** |

The last row is the only filter about labels rather than about whether a
document is usable, and it costs the most.

**The rule.** A case is kept only if all its Westlaw keys map to one taxonomy
code. Several keys are fine — 7 of the 44 kept cases carry two keys meaning the
same risk category (`k143.5` and `k147(3)` both map to 2.2). Excluded is a case
whose keys span two codes.

### Step 1 — which clauses were disputed (Opus 5, high effort, one call per case)

The call carries the **numbered opinion plus every registered contract of the
case**, the risk codes the case was selected under, and the headnote text. The
model returns which clauses the parties disputed, where each sits, and — for each
one — the passage of the opinion showing the dispute. That last requirement keeps
clause selection tied to the court's own words rather than to what looks risky.

**Returning no clauses is a valid answer**, and happens when the disputed
agreement was never filed.

### Step 2 — every clause of a contract (Opus 5, high effort, one call per contract)

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

Three over-capture detectors run as **flags** — clauses out of document order,
overlapping spans, and a clause longer than 10× the contract's median. They are
printed and stored; none of them rejects anything.

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
| `citation`, `taxonomy`, `key` | the case and the risk category, from the Westlaw key |
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

| step | model | input | output | |
|---|---|---:|---:|---:|
| 0b layout | Sonnet 5 | 1,500,540 | 10,401 | $4.66 |
| 1 extract | Opus 5 | 3,132,579 | 104,269 | $18.27 |
| 2 inventory | Opus 5 | 2,378,231 | 720,200 | $29.90 |
| **total** | | | | **$52.82** |

Step 0 and `build_dataset.py` make no model calls, so the dataset rebuilds from
the stored artifacts without an API key. Every call's full prompt, response and
token usage is kept under `output/llm_logs/`.

---

## 6. Known limits

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

# The pipeline, and what it produced

A dataset of contract clauses labelled **risky** or **not risky**, where *risky*
means a United States federal court construed the clause in a written opinion.
Every clause is verbatim text cut out of the scanned filing it was attached to.

---

## 1. What a label means

**POSITIVE.** The opinion shows the two sides fought over this clause and the
court discussed it. Reaching litigation at all is what makes a clause risky, so
a clause the court examined and *upheld* counts exactly as much as one it struck
down. How the case came out plays no part in the label.

**NEGATIVE.** Every other clause of every contract filed in the *same case*.

A positive's own contract always contributes negatives, so the two classes share
the drafter, the domain, the era and the OCR condition — the only systematic
difference between them is the one being labelled. The other agreements of the
case contribute negatives as well: a case commonly files several instruments and
the court reaches only some of them, and the rest were before the court and were
not construed. Sixteen of the 67 contracts contain no positive at all, which
makes them the only documents here in which the right answer is "nothing to
flag".

**The label does not come from a model.** It comes from the **Westlaw Key
Numbers** the case was filed under. Twelve keys map onto six risk-type codes:

| code | risk type |
|---|---|
| 1.1 | Lexical ambiguity — a word or phrase is open to more than one reasonable reading |
| 1.2 | Mechanical error — a mistake in writing, grammar, spelling or punctuation |
| 1.3 | General-vs-specific / list scope — a catch-all sits against enumerated specifics |
| 2.1 | Conflicting clauses — this clause cannot be squared with another |
| 2.2 | Whole-contract coherence — the meaning only emerges when the contract is read whole |
| 2.3 | Recitals vs operative text |

The model is *handed* the codes the case was selected under and may only choose
among them. A code outside that set is rejected. Every row carries the specific
keys it was selected under, so a label can be traced back to the headnotes.

---

## 2. The core principle: locate, don't transcribe

**No model ever writes dataset text.**

A model's answer is a *pointer*, never a quotation. For each clause it returns
four fields:

- `start_line`, `end_line` — a coarse line window;
- `head` — the clause's first 8 words, copied exactly off the scan;
- `tail` — its last 8 words, likewise.

`lib.locate()` then does the work: it widens the window by 5 lines each side,
matches the anchors against the raw file, **snaps the boundary to where the
anchors actually are**, and slices the characters out of the file. An anchor
scoring below 0.75 means the model pointed at the wrong place and the clause is
rejected with its reason recorded.

This buys three things at once. The anchors *prove* the range is real; they
*repair* an off-by-a-few line count; and they cut **inside** a line, which is
how two lettered sub-clauses that OCR ran onto one line are told apart.

The extracted span is then normalised — page furniture and docket stamps
dropped, words the scanner split across a line break rejoined, whitespace
collapsed. Nothing else. **No spelling is corrected, no number is repaired, no
missing word is restored.** Where the scan reads `givr. thr. rlr.filulting
pilrty`, that is what the dataset carries. Models under evaluation should meet
the documents as they exist.

---

## 3. The four steps

### Step 0 — build the corpus (no LLM)

Join the Westlaw headnotes, the opinion text and the docket linking sheet, and
keep the cases in scope. Register each extracted contract to
`output/contracts/<id>.md` and `output/contracts.json`.

The contract text arrives already prepared: the filings were downloaded and
OCR'd with `ocrmypdf --force-ocr`, a model said which file holds which named
agreement, and the file was sliced to that agreement's own lines — a **verbatim
line-range cut**, so a model chose the boundaries but no model wrote the words.
A duplicate check (8-word shingle containment at 0.90) catches the same document
filed twice under two names.

### Step 0b — reject two-column scans (Sonnet 5, low effort, one call per contract)

Some filings are printed in two side-by-side columns, and OCR reads such a page
straight across: every output line carries the left column and the right column
run together, from two passages that have nothing to do with each other. Nothing
downstream can see this — the anchors match, the line range is real, and the
"clause" is alternating halves of two different clauses.

Two independent detectors run, and **either one rejects**:

- **A gutter score.** A run of blank space at the same column position on many
  *consecutive* lines. Persistence is the discriminator: a body column holds its
  gutter for dozens of lines, a letterhead for five. Costs nothing.
- **A model call.** The document is sampled as windows of consecutive lines
  spread from the first line to the last, and the model must **quote the
  interleaved lines back verbatim**, so every rejection can be looked up in the
  file.

Both run because they fail differently. The gutter is blind to a document whose
left column is often empty, since the run of blank space breaks wherever there
is nothing to its left. The model is blind to a two-column passage that occupies
a small fraction of a very long filing, since it reads a sample. Between them
they cover 8 of the 117 contracts, four of which are insurance policies whose
endorsement pages are set in two columns.

Nothing is deleted. `output/layout.json` records the verdict, both scores and
the evidence; step 1 skips the contract.

### Step 1 — which clauses were disputed (Opus 5, high effort, one call per case)

The call carries the **numbered opinion plus every registered contract of that
case**, the risk types the case was selected under, and the headnote text. The
model says which clauses the parties disputed, where each sits, and — for each
one — the passage of the opinion showing the dispute. That last requirement is
what keeps clause selection tied to the court's own words rather than to what
looks risky to a model.

**Returning no clauses is a valid answer** and happens legitimately: the
disputed agreement was simply never filed.

### Step 2 — every clause of a contract (Opus 5, high effort, one call per contract)

This step enumerates a contract's clauses in document order, producing the
negatives. It runs on every contract step 1 was shown, for every case step 1
processed — so no positive lacks negatives from its own document, and the
agreements the court never reached are inventoried too.

Three over-capture detectors run as **flags** — clauses out of document order,
clauses whose spans overlap, and a clause longer than 10× the contract's median.
They are printed and stored; **none of them rejects anything.**

### `build_dataset.py` (no LLM)

Assembles the rows, then refuses to write unless the data survives four checks:

- every row is **re-cut from disk** at its recorded character span and must
  reproduce `clause_text` exactly;
- no two rows occupy the same span of the same contract;
- no text carries both labels;
- a negative whose lines meet a positive's is dropped, not labelled.

A negative in a contract that holds no positive of its own carries the **case's**
taxonomy code — still a Westlaw key, and unambiguous because every case in scope
is filed under exactly one.

---

## 4. What was produced

### Corpus

| | |
|---|---:|
| contracts registered | 117 over 68 cases |
| rejected as two-column scans | 8 |
| working set (single-column, single risk code) | **67 contracts over 39 cases** |
| cases run through step 1 | 39 |
| cases yielding at least one positive | 38 |
| contracts inventoried in step 2 | 67 |
| clauses located in step 2 | 6,838 (70 rejected on their anchors) |
| negatives dropped for meeting a positive | 147 |

### The dataset

```
6,835 rows  |  144 positive / 6,691 negative  (2.1% positive)
39 cases    |  67 contracts                   |  8.9 MB
```

51 of the 67 contracts contain at least one positive; the remaining 16 are
wholly negative.

**Integrity**

| | |
|---|---|
| rows re-cut from disk and reproduced exactly | 6,835 / 6,835 |
| anchor score | worst 0.75, mean 0.997, **6,708 exact** |
| rows sharing a span | 0 |
| texts carrying contradictory labels | 0 |
| positive/negative leakage | 0 |

**Clause length, in characters**

| | mean | p25 | median | p75 | max |
|---|---:|---:|---:|---:|---:|
| positive | 813 | 304 | 607 | 1,116 | 3,836 |
| negative | 564 | 202 | 372 | 691 | 18,970 |

Positives run longer: P(a positive is longer than a negative) = **0.655**, where
0.50 would mean length carries no information. This is a property of the data,
not an artifact — the clauses parties take to court are the long, qualified,
heavily conditioned ones. Report a length-only baseline alongside any model
benchmarked here.

**Risk types present**

| code | rows | of which positive |
|---|---:|---:|
| 1.1 lexical ambiguity | 5,537 | 125 |
| 2.2 whole-contract coherence | 1,043 | 15 |
| 1.3 general-vs-specific | 255 | 4 |

Only three of the six codes appear, because the working set is restricted to
cases filed under a **single** risk code, and single-code cases are dominated by
1.1.

**Clauses per contract:** min 6, median 63, max 418.

### Columns

| column | |
|---|---|
| `citation`, `taxonomy`, `key` | the case and the risk type, from the Westlaw key |
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

### Cost

| step | model | input | output | |
|---|---|---:|---:|---:|
| 0b layout | Sonnet 5 | 1,500,540 | 10,401 | $4.66 |
| 1 extract | Opus 5 | 3,132,579 | 104,269 | $18.27 |
| 2 inventory | Opus 5 | 2,378,231 | 720,200 | $29.90 |
| **total** | | | | **$52.82** |

Steps 0 and `build_dataset.py` make no model calls, so the dataset can be
rebuilt from the stored artifacts without an API key. Every call's full prompt,
response and token usage is kept under `output/llm_logs/`.

---

## 5. Known limits

Stated plainly, because they are real costs.

- **Over-capture between two correct anchors is undetectable.** A range that
  starts at the right clause and ends at the right clause but swallows an
  intervening one passes every check. 22 contracts carry a flag for it; the
  flags reject nothing, and the longest negative in the dataset (18,970
  characters) is the clearest candidate.
- **OCR sometimes duplicates text across a page boundary**, and the anchor span
  then swallows both copies. The two copies differ (`investment finds` vs
  `investment funds`), so no text rule finds them safely, and none is applied.
- **`clause_name` is free text and is never verified** against what the anchors
  located.
- **The opinion passage is range-only.** It is cut from reported line numbers
  with no anchor. It is context, not label-bearing text.
- **Clause selection is not reproducible.** One sample per case; a second sample
  would not return an identical list.
- **A contract split across two OCR files** is registered as two documents, and
  a clause spanning the split cannot be located.

See `docs/DESIGN.md` for the reasoning behind each design decision and the
measurements every threshold was set from.

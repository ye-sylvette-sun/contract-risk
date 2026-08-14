# The dataset pipeline — locate-and-extract design

The dataset is a set of **contract clauses labelled risky or not risky**, where
"risky" means *a federal court construed this clause as carrying a specific
construction defect*. The label is never a model's opinion: it comes from the
Westlaw Key Number the case was filed under, and every positive points at the
passage of the opinion where the court did the construing.

Every model call is **one stateless request to the Claude Messages API** — no
tools, no agent loop, no multi-turn session, no memory between calls. The dataset
must not be built by the same kind of system it is used to evaluate.

Vocabulary is **contract** and **clause** throughout — never "instrument" or
"provision".

---

## 1. The one change from the previous design

The model no longer writes clause text. It **locates** clauses; the pipeline
**extracts** them.

| | old | new |
|---|---|---|
| model returns | line range **+ the clause transcribed with OCR damage repaired** | line range **+ a short verbatim anchor at each end** |
| dataset text comes from | the model's transcription, after `verify()` proved it aligned to the source | the contract file, sliced between the anchors, whitespace-normalised |
| a mismatch means | the clause is **dropped** | the boundary is **snapped** to where the anchor actually is |

### Why

Measured over the 960 clauses the old pipeline kept:

| | |
|---|---|
| words the repair changed | **1.8 – 2.1 %** |
| clauses byte-identical to raw once whitespace is normalised | **45 %** (442 of 960) |
| step-2 output tokens spent on clause text | **57 %** — $4.83 of $8.42 |
| clauses lost to transcription mismatch | **440**, plus 23 rejected positives |

We were paying 57 % of step 2's output budget, and destroying roughly 40 % of the
data, to change 1.8 % of words — changing nothing at all in nearly half of cases.
The repair was not wrong often; it was simply not worth what it cost to prove.

Three further failures go away with it:

- **`MAX_OUTPUT` truncation.** Three contracts are unusable today because
  transcribing them exceeds the 128k output ceiling. Locating them does not.
- **Content-filter blocks.** `11FSupp3d1062` has been blocked three times,
  deterministically, while transcribing 233k chars of an insurance policy. Line
  ranges and eight-word anchors are a much smaller target. *Expected to help, not
  proven.*
- **Non-uniform data loss.** `verify()` dropped 2 clauses from the clean `328`
  agreements and 240 from the degraded `35FSupp3d725_confirmations`. Badly
  scanned contracts contributed a tenth of the negatives that clean ones did, so
  the surviving text was quality-filtered by a filter whose strength varied per
  document. Removing repair removes that bias rather than adding noise.

### Why the OCR noise is acceptable

Positives and negatives come from the **same contract** by design. Scan quality
is therefore matched across the label within every document, so OCR noise cannot
become a shortcut a classifier learns. This property is what makes the trade
safe, and it is the reason step 2 must keep running on *every* contract that
produced a positive.

---

## 2. Anchors

The model returns, for each clause, four locating fields:

| field | meaning |
|---|---|
| `start_line`, `end_line` | the coarse line window in the named file |
| `head` | the clause's **first 8 words, copied exactly as the OCR shows them** |
| `tail` | the clause's **last 8 words, copied exactly as the OCR shows them** |

`ANCHOR_WORDS = 8`. A clause shorter than 16 words returns the whole thing as
`head` and an empty `tail`.

The anchors are explicitly **not repaired**. Where the scan says
`givr. thr. rlr.filulting pilrty`, that is what the anchor says. This is the one
place the prompt must fight the model's instinct, and it is a far easier
instruction to follow for 8 words than for a 900-word clause.

### The anchors do three jobs

**1. They prove the range is real.** An anchor that matches nothing near the
claimed window means the model pointed at the wrong place — the failure that
matters, and the only one `verify()` was ever really catching. Word-for-word
alignment of the whole clause was an expensive way to check a boundary.

**2. They repair the range.** Match each anchor against the claimed window
widened by `SLACK` lines on each side, take the best-scoring position, and
**snap the boundary to it**. Today a miscounted line kills a clause; here it is
corrected. Models are known to miscount lines — the old design conceded this with
a `±RETRY_LINES` retry, and this replaces that hack with an actual fix.

**3. They cut inside a line.** Extraction is the character span from where `head`
begins to where `tail` ends, which may start and end mid-line. This solves a case
the old design could not: `lib.CLAUSE` tells the model that when the OCR runs
lettered sub-clauses together on one line it should give each the same range and
"let the text tell them apart". Under range-only extraction those sub-clauses
would extract *identical text* and trip `build_dataset`'s duplicate assertion.
Anchors separate them.

### Matching rule

Compare under the existing `_words` folding (case-folded, curly quotes and
dashes normalised, markdown punctuation dropped) so an anchor is not lost to a
stray pipe. Score with `difflib.SequenceMatcher` over word sequences and accept
above `ANCHOR_MATCH = 0.75`; below it, reject the clause and record why.

The threshold is a locating tolerance, not a text tolerance — the text comes
from the file regardless of how well the anchor scored. It exists because a model
copying 8 words from damaged OCR will occasionally slip a character, and that
must not cost a clause.

---

## 3. LLM response format

Both steps use structured output — `output_config.format` with a
`json_schema`, so the response is validated at the API boundary and the model
retries on a mismatch. The schemas live beside their prompts as
`prompts/<step>.schema.json`. `additionalProperties: false` and a complete
`required` list are mandatory: without them the model may return a field the
pipeline silently ignores.

Note what is **absent** from both: no `text` field. That single removal is the
whole design change.

### Step 1 — `prompts/extract.schema.json`

One call per case. Returns the clauses the court construed, across every contract
filed in that case.

```json
{
  "type": "object",
  "properties": {
    "case_desc": {
      "type": "string",
      "description": "One line: the parties, and what the construction dispute was about."
    },
    "clauses": {
      "type": "array",
      "description": "The clauses the court construed. An empty list is a valid answer when the construed contract is not in the record.",
      "items": {
        "type": "object",
        "properties": {
          "clause_name": {
            "type": "string",
            "description": "Short name including the section number, e.g. 'Section 8 — Indemnity'."
          },
          "contract_id": {
            "type": "string",
            "description": "The contract_id of the contract this clause came from, exactly as tagged in the document block."
          },
          "start_line": {
            "type": "integer",
            "description": "First line the clause occupies in that contract."
          },
          "end_line": {
            "type": "integer",
            "description": "Last line the clause occupies in that contract."
          },
          "head": {
            "type": "string",
            "description": "The clause's first 8 words, copied EXACTLY as the lines show them. Do not repair the OCR here. If the whole clause is under 16 words, put all of it here."
          },
          "tail": {
            "type": "string",
            "description": "The clause's last 8 words, copied EXACTLY as the lines show them. Do not repair the OCR here. Empty string if the whole clause is already in head."
          },
          "opinion_comment_start_line": {
            "type": "integer",
            "description": "First line in the OPINION of the passage where the court construes this clause."
          },
          "opinion_comment_end_line": {
            "type": "integer",
            "description": "Last line in the OPINION of that passage."
          }
        },
        "required": ["clause_name", "contract_id", "start_line", "end_line",
                     "head", "tail",
                     "opinion_comment_start_line", "opinion_comment_end_line"],
        "additionalProperties": false
      }
    }
  },
  "required": ["case_desc", "clauses"],
  "additionalProperties": false
}
```

A filled response, on a clause whose scan is damaged — note that the anchors
carry the damage through rather than mending it:

```json
{
  "case_desc": "Newspaper publisher and pressmen's union dispute whether paid leave counts toward pension contributions.",
  "clauses": [
    {
      "clause_name": "Section 13-I.1 — Pension Fund contributions",
      "contract_id": "303FSupp3d236_multiemployer_collective_bargaining_agreement",
      "start_line": 4471,
      "end_line": 4489,
      "head": "18-1. 1. the publisher shall contribute to the",
      "tail": "as provided in the agreement and declaration of trust.",
      "opinion_comment_start_line": 212,
      "opinion_comment_end_line": 247
    }
  ]
}
```

### Step 2 — `prompts/inventory.schema.json`

One call per winning contract. Returns every clause of that one contract, so it
needs no `contract_id` and no opinion fields.

```json
{
  "type": "object",
  "properties": {
    "clauses": {
      "type": "array",
      "description": "Every substantive clause of the contract, in document order.",
      "items": {
        "type": "object",
        "properties": {
          "name": {
            "type": "string",
            "description": "Short name including the clause number if it has one."
          },
          "start_line": {
            "type": "integer",
            "description": "First line the clause occupies."
          },
          "end_line": {
            "type": "integer",
            "description": "Last line the clause occupies."
          },
          "head": {
            "type": "string",
            "description": "The clause's first 8 words, copied EXACTLY as the lines show them. Do not repair the OCR here. If the whole clause is under 16 words, put all of it here."
          },
          "tail": {
            "type": "string",
            "description": "The clause's last 8 words, copied EXACTLY as the lines show them. Do not repair the OCR here. Empty string if the whole clause is already in head."
          }
        },
        "required": ["name", "start_line", "end_line", "head", "tail"],
        "additionalProperties": false
      }
    }
  },
  "required": ["clauses"],
  "additionalProperties": false
}
```

A filled response, showing the shared-line case §2 describes — two lettered
sub-clauses the OCR ran onto one line, identical ranges, separated only by their
anchors:

```json
{
  "clauses": [
    {
      "name": "Section 4 — Term",
      "start_line": 118,
      "end_line": 124,
      "head": "4. term. this agreement shall commence on the",
      "tail": "unless sooner terminated under Section 11 hereof."
    },
    {
      "name": "Section 5(a) — Renewal at Buyer's option",
      "start_line": 126,
      "end_line": 126,
      "head": "(a) buyer may renew for one additional",
      "tail": "by written notice ninety (90) days prior;"
    },
    {
      "name": "Section 5(b) — No renewal after default",
      "start_line": 126,
      "end_line": 126,
      "head": "(b) no renewal shall be effective if",
      "tail": "buyer is then in default hereunder."
    }
  ]
}
```

### What the response does *not* guarantee

- **The opinion passage is still range-only and unchecked.** `opinion_comment` is
  extracted by `lib.window` from `opinion_comment_start_line`/`_end_line` with no
  anchor and no verification, exactly as before. It is context, not label-bearing
  text, so it was never verified — but the asymmetry should be named rather than
  discovered later. Adding anchors there is cheap and is left as an open question.
- **`clause_name` is free text and is never checked** against what the anchors
  locate. A clause named `Section 8` whose anchors land on Section 9 produces a
  correctly-extracted clause under a wrong name.
- **Line numbers are advisory.** They are a coarse locator that anchor snapping
  is allowed to override (§2). A response whose ranges are all off by a constant
  offset still yields a correct dataset.

## 4. Extraction and normalisation — deterministic, no model

Applied to the character span between the snapped anchors:

| stage | what it does | status |
|---|---|---|
| page furniture | already blanked by `strip_ocr` at step 0 — lines are blanked, never removed, so line numbers stay stable | **exists** |
| de-hyphenation | rejoin a word the scanner split across a line break (`Decla-` / `ratlons`); continuation must be lowercase and on a *different* source line | **exists** (`_dehyphenate`) |
| whitespace | collapse runs of spaces, join lines with a single space, strip | new, trivial |

Nothing else. No substitution, no number correction, no label mending. What the
scan says is what the dataset carries.

---

## 5. What is unchanged

Everything that made the old pipeline defensible carries over untouched:

- **Step 0** — corpus construction, no LLM. `cases.json`, `contracts.json`,
  `contracts/<cid>.md`, `opinions/<id>.txt` are all reusable as they stand; the
  44-case / 74-contract funnel does not move.
- **The label** comes from the Westlaw key, never from a model.
- **`lib.CLAUSE`** — one clause definition, injected into both prompts as
  `{clause_def}` so neither can hold a copy that drifts. Coverage stays
  step-specific; boundaries do not.
- **Every positive must cite the opinion passage** where the court construes it.
  Returning no clause remains a valid answer.
- **Step 2 runs on every contract that produced a positive**, so a positive is
  never left without negatives from its own document.
- **Step 3 set arithmetic** — a negative is excluded iff its line window
  intersects a positive's (`lib.overlaps`). Every exclusion printed.
- **Resumability** — steps 1 and 2 write after every call and re-run only what is
  missing.
- **The four-section prompt layout** — `SYSTEM` / `DOCUMENT` / `INSTRUCTIONS` /
  `TASK`, with instructions after the document.

---

## 6. What is deleted

From `lib.py`: `_align`, `_lev`, `_number_changed`, `_skippable`,
`_without_furniture`, `_is_furniture`, `_is_grit`, and the constants `MAX_EDIT`,
`FURNITURE`, `RETRY_LINES`. `verify()` is replaced by `locate()`.

From both prompts: the entire **OCR REPAIR** instruction block, and from
`lib.CLAUSE` the paragraph `COPY A BROKEN LABEL, DO NOT MEND IT` — with no
transcription there is no label to mend. The rules about *which lines to give*
stay and become more important, not less.

`_dehyphenate` survives, moving from the verifier into the normaliser.

---

## 7. New risks, stated plainly

These are real costs of the change, not hypotheticals.

**A wide range with correct anchors is undetectable.** If the model gives a
range that starts at the right clause and ends at the right clause but swallows
an intervening one, both anchors match and the extraction silently contains too
much. `verify()` would have caught this as added words. Proposed mitigations, to
be measured rather than assumed:

- compare extracted length against neighbouring clauses in the same contract and
  flag outliers;
- require step 2's clauses to be non-overlapping and in document order, since it
  claims to enumerate the whole contract;
- flag any clause whose range exceeds some multiple of the contract's median.

**Two-column OCR now corrupts silently.** `11 F.Supp.3d 1062` §G.3.a sits at
lines 2836–7 where two columns interleave. `verify()` rejected it loudly; anchor
matching will accept it and extract both columns interleaved. There is no cheap
detector for this. It must be counted and reported, not hidden.

**The dataset is less readable.** Rows will contain
`mewerneanane nf tha yachlichae ehall santimers un srgwse`. Accepted
deliberately — see §1.

---

## 8. Repair, if it is ever wanted again

Do not delete the idea, invert it. Repair becomes an **optional third pass over
the ~960 clauses that survive**, writing a separate `clause_text_repaired`
column. A 700-char clause is cheap to repair; a 264k-char contract is not.

The point of the inversion: today a failed repair deletes a row, because
selection and transcription happen in the same call. Split them and a failed
repair costs a column value and nothing else.

---

## 9. Expected cost

Anchors are ~16 words against a ~93-word average clause, so they restore about
17 % of the transcription tokens.

| | old | new |
|---|---|---|
| step 2 output, 16 contracts | 336,990 tok / $8.42 | ~180,000 tok / **~$4.5** |
| step 1 | ~$0.31 per case | roughly unchanged — it was never output-bound |
| clauses lost to text mismatch | 440 of ~1,400 | expected near zero |

Step 2 is two-thirds of total spend, so this is roughly a **45 % cut overall**,
with the truncation ceiling and one content-filter block removed as well.

---

## 10. Validation before spending anything

The old logs in `_github_repo_bak/output/llm_logs/` contain full model responses
with complete clause text for 1,400 clauses. The first 8 and last 8 words can be
sliced straight out of them to synthesise anchors, and the matcher, the snapping
and the normaliser can all be scored offline at **zero API cost** — the same
replay method that chose the previous round of `verify()` changes and rejected
three candidates.

What to measure before writing a prompt:

1. what fraction of synthesised anchors match at the claimed boundary;
2. how often snapping moves a boundary, and by how many lines;
3. how often extracted text differs from the old `clause_text_raw` — it should be
   nearly always identical, and any difference is a bug in the slicing;
4. how many clauses two-column interleave would silently corrupt.

Only then write the prompts and run a small batch.

---

## 11. Files

```
prompts/extract.{md,schema.json}      step 1 — locate construed clauses
prompts/inventory.{md,schema.json}    step 2 — locate every clause
src/lib.py                            locate(), strip_ocr(), normalise(), CLAUSE, ask()
src/step0_corpus.py  src/step1_extract.py  src/step2_inventory.py  src/build_dataset.py

output/cases.json           kept cases, with keys, headnotes and taxonomy code
output/contracts.json       the contract registry
output/contracts/<cid>.md   contract text, OCR furniture stripped
output/opinions/<id>.txt    opinion text            (gitignored, derived from data/)
output/clauses.json         step 1 — positives, and what was rejected
output/inventory.json       step 2 — every clause of the winning contracts
output/llm_logs/<step>/     full prompt, response and usage for every call
output/dataset.csv          the dataset
```

`dataset.csv` columns:

```
citation  taxonomy  key  clause_id  clause_name  label  provenance  case_desc
contract_id  contract_file  source_lines  source_span  clause_text  anchor_score
opinion_comment
```

`clause_text` is now the normalised extraction. `source_span` records the
character offsets the anchors snapped to, and `anchor_score` the match quality —
together they make every row's provenance reproducible from the contract file
alone, without the model's output.

---

## 12. Open questions

- **Paragraph structure.** Collapsing a clause to one line loses the shape of a
  numbered sub-list. Worth keeping single newlines between source lines instead?
- **`ANCHOR_WORDS = 8`** is a guess. Measurable offline against the old logs.
- **Wide-window detection** (§7) — which of the three mitigations actually earn
  their place.
- **Non-reproducibility of clause *selection*** is untouched by this design.
  Step 1 returned 2 positives for `131 F.Supp.3d 635` on one run and 0 on
  another. That is a sampling question — several samples per case with a union or
  a vote — and it remains open.
- **`11FSupp3d1062`** — whether the content-filter block actually clears.

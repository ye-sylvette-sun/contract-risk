# The dataset pipeline

How `output/dataset.csv` is built, as the code in `src/` builds it.

---

## 1. What the dataset is

A set of **contract clauses labelled risky or not risky**, cut from contracts
filed on United States federal court dockets. The label is grounded in what a
court did with the clause — never in a model's opinion of how the clause reads.

### What makes a clause positive

> **A clause is positive when the court's opinion shows the two sides disputed
> it and the court discussed it. How the court decided does not matter.**

The reasoning is the whole point of the dataset. We do not want a clause in a
contract to lead to a court dispute *at all*. Whether we would go on to win that
dispute is beside the point — the litigation is itself the cost. A clause that
had to be argued over in front of a federal judge has already failed at its job,
even where the judge ultimately read it the way its drafter intended.

So every one of these is positive:

- a clause the court examined and **upheld**;
- a clause the court held **clear**, **plain** or **unambiguous** — that the
  parties needed a court to tell them so is precisely the failure;
- a clause the court found ambiguous, or construed against its drafter.

What is required is evidence in the opinion itself, of either kind: the parties
advancing **competing readings** of the clause, or the court **discussing or
construing** it in its own analysis. A clause quoted in passing, recited as
background, or cited with no dispute and no discussion is not positive.

Every positive must cite the passage of the opinion that shows the dispute. That
passage is stored on the row as `opinion_comment`, so the label can always be
audited against the court's own words. One qualifier applies: the dispute must
fall under the risk type the case was selected for (§2). In practice this
excludes very little — most cases are filed under a single type, and the
disputes the opinion discusses are usually that type.

### What makes a clause negative

> **Every other clause of the same contract.**

"Other" means every clause whose line window does not intersect a positive's.
That is exact set arithmetic (`lib.overlaps`), not a similarity judgement: a
section containing a disputed sub-clause contains its lines, so it is excluded,
and every exclusion is printed.

### Why negatives come from the same contract

Positives and negatives are cut from the **same document**, so the two classes
are matched on everything except the label:

| matched | so a classifier cannot win by |
|---|---|
| the document | recognising document style or subject matter |
| the OCR condition | recognising scan quality |
| the granularity | recognising clause length |

This matched-condition principle governs several decisions further down, and it
is what makes the design's other trade-offs safe.

---

## 2. The label's provenance — the Westlaw key

A case enters the corpus because it was filed under one of twelve Westlaw Key
Numbers. Those keys map to six risk types (`lib.KEYS`, `lib.TYPES`):

| code | risk | keys |
|---|---|---|
| 1.1 | lexical ambiguity or vagueness | k143(2), k152, k159 |
| 1.2 | mechanical error (grammar, spelling, punctuation) | k157, k158 |
| 1.3 | general-vs-specific / list scope | k156, k155 |
| 2.1 | conflicting clauses | k162 |
| 2.2 | whole-contract coherence | k143.5, k147(3), k161 |
| 2.3 | recitals vs operative text | k160 |

Category **1** is an intrinsic defect visible in the clause's own wording;
category **2** arises from the clause's relationship to the rest of the contract.

**The model never chooses the label.** The codes a case carries are handed to it
as facts, and `step1_extract.check()` rejects any code outside that set. Naming
which of them a dispute falls under is an integrity check on *which* risk type —
never a filter on *whether* a clause was disputed. Nothing in the pipeline may
turn on how the case came out.

Each row records both the code (`taxonomy`) and the keys it came from (`key`),
so a label can be traced back to the headnotes that produced it.

---

## 3. The core principle: locate, don't transcribe

**The model never writes clause text.** It *locates* clauses; the pipeline
*extracts* them.

For each clause the model returns four locating fields:

| field | meaning |
|---|---|
| `start_line`, `end_line` | the coarse line window in the named file |
| `head` | the clause's first `ANCHOR_WORDS` words, **copied exactly as the OCR shows them** |
| `tail` | the clause's last `ANCHOR_WORDS` words, likewise |

`ANCHOR_WORDS = 8`. A clause shorter than 16 words returns the whole thing as
`head` and an empty `tail`.

The anchors are explicitly **not repaired**. Where the scan reads
`givr. thr. rlr.filulting pilrty`, the anchor says that. This is the one place
the prompt must fight the model's instinct to tidy, and it is far easier to
follow for eight words than for a nine-hundred-word clause.

Dataset text is then sliced out of the contract file between the anchors. What
the model writes only ever says *where*; it is never carried into a row.

### Why the OCR damage is kept rather than repaired

Rows will contain `mewerneanane nf tha yachlichae ehall santimers un srgwse`.
That is deliberate. Repairing the text means the model must transcribe it, and
transcription is expensive to buy and expensive to verify — the verification
being the real cost, since proving a repair is honest means aligning it against
the source word by word, and every alignment failure destroys a clause.

It is also unnecessary. By §1, scan quality is matched across the label inside
every document, so OCR noise cannot become the shortcut a classifier learns.

### What the anchors buy

**They prove the range is real.** An anchor matching nothing near the claimed
window means the model pointed at the wrong place — the failure that matters.

**They repair the range.** The claimed window is widened by `SLACK` lines on
each side, the best-scoring position wins, and the boundary snaps to it. A
miscounted line is corrected rather than fatal.

**They cut inside a line.** Extraction runs from where `head` begins to where
`tail` ends, which may start and end mid-line. This is what separates two
lettered sub-clauses the OCR ran onto a single line: they share a line range and
are told apart by their anchors alone. Without it they would extract identical
text and collide.

### Why every clause is a sub-clause

`lib.CLAUSE` requires the **smallest unit that states a complete obligation,
right, grant, limitation, condition or definition on its own**. Where a numbered
section is a list of lettered sub-clauses, each sub-clause is a clause and the
section is not; where a section is not subdivided, the section is the clause.

That rule is injected into **both** prompts from one place, so the two steps
cannot drift apart. If they did, positives and negatives would be different
kinds of object and clause length would correlate with the label — the third row
of the table in §1.

---

## 4. The pipeline

Every model call is **one stateless request to the Claude Messages API** — no
tools, no agent loop, no multi-turn session, no memory between calls. The
dataset must not be built by the same kind of system it is used to evaluate.

### Step 0 — `step0_corpus.py` (no LLM)

Two jobs.

**Link.** Join three sources and keep the cases worth spending money on:

| input | supplies |
|---|---|
| `data/wl-headnotes-parsed/<key>/citations.csv` | cases under each of the 12 keys, with headnotes |
| `data/opinions-case-dot-law.csv` | full opinion text |
| `data/Agreements Docket-Opinion Linking Data.xlsx` | whether a contract can actually be downloaded |

A case is in scope when it is under one of the twelve keys, its opinion text is
available, **and** the linking sheet shows at least one downloadable entry
document. Doing the acquisition match first is what makes it affordable to hand
whole opinions to a model later instead of regex-harvested snippets.

**Register.** Write every OCR'd file of those cases' bundles to
`output/contracts/<cid>.md`, stripped of OCR furniture, and record it in
`output/contracts.json`. Duplicates — the same exhibit attached to two filings —
are collapsed at `difflib` ratio ≥ `DUP`.

Which registered documents are *contracts* is deliberately not decided here,
because deciding it needs a model. Step 1 is handed all of them and cites only
what the parties disputed; step 2 — the expensive step — runs only on the
documents that won. Court filings therefore cost input tokens in step 1 and
nothing after that.

The contract id is built from the citation and the source filename, never from a
name a model gave it, and a `taken` set appends a numeric suffix so one
registration can never silently overwrite another.

### Step 1 — `step1_extract.py` (one call per case)

Input: the numbered opinion plus every registered document for that case, each
in its own `---------- CONTRACT <cid> START ----------` block, plus the risk
types the case was selected under and the headnote text.

Output per clause: `clause_name`, `taxonomy`, `contract_id`, the line range, the
two anchors, and the opinion passage showing the dispute. Each is located
(§5) and rejected with a reason if an anchor does not match.

Returning **no clauses is a valid answer** and happens legitimately: the
disputed contract simply was not in what got downloaded.

### Step 2 — `step2_inventory.py` (one call per winning contract)

A winning contract is one step 1 found a positive in. This step lists every
clause of it, in document order, so the negatives are cut from the same document
as the positives. It runs on **every** contract that produced a positive, so no
positive is left without negatives from its own document.

It also runs three over-capture detectors (§8) as **flags**: printed and stored
in `inventory.json`, rejecting nothing.

### `build_dataset.py` (no LLM)

Positives from step 1, negatives from step 2 by the set arithmetic in §1, then
validation that refuses to write on failure:

- no two rows share a `clause_text`;
- **every row re-cuts from disk** — reading its contract file at the recorded
  `source_span` and normalising must reproduce `clause_text` exactly;
- every positive has a non-empty `opinion_comment`.

That second check is the gate. Nothing is taken on trust from a step artifact.

---

## 5. `locate()` — the only route to dataset text

```
locate(text, start, end, head, tail) -> (record, None) | (None, why)
```

1. Widen the claimed window by `SLACK` lines on each side.
2. Tokenise it, keeping each token's character offset into the file. Tokens are
   folded (case, curly quotes, dashes, markdown punctuation) so an anchor is not
   lost to a stray pipe the model did or did not copy.
3. Score every run of `len(head)` tokens with `difflib.SequenceMatcher`. Ties go
   to the run nearest the position the model claimed, so a clause whose opening
   words repeat elsewhere snaps to the copy it named.
4. Search `tail` from the head onward, so it can never land before it, and take
   the **earliest** best-scoring run — over-capture is the failure this design
   cannot otherwise see (§8).
5. Reject below `ANCHOR_MATCH = 0.75`. Otherwise the span is from where `head`
   begins to where `tail` ends.

`ANCHOR_MATCH` is a **locating** tolerance, not a text tolerance — the text comes
from the file regardless of how well the anchor scored. It exists because a model
copying eight words out of damaged OCR will occasionally slip a character, and
that must not cost a clause.

The record carries the character span, the line range that span covers, the match
score, and the extracted text. `source_span` and `anchor_score` reach the CSV, so
every row is reproducible from the contract file alone.

### Extraction and normalisation — deterministic, no model

Applied to the character span between the snapped anchors:

| stage | what it does |
|---|---|
| stamps | drop a source line that is entirely a page marker (`p. 3`, `Page 3 of 12`) or a zero-padded Bates number (`ROADLINK 00066`), and no longer than `FURNITURE` characters |
| de-hyphenation | rejoin a word the scanner split across a line break (`perfor-` / `mance`); the continuation must be lowercase and on the very next line |
| whitespace | collapse runs of spaces, join lines with a single space, strip |

Nothing else. No substitution, no number correction, no label mending. What the
scan says is what the dataset carries.

Both stamp patterns are **labelled**; a bare number on its own line is not one of
them. A lone `1993` or `5,000` is as likely to be a flattened table cell as a
page number, and dropping a figure the contract states is the one thing this
pipeline must never do. A stray page number left in the text is the cheaper
mistake.

---

## 6. Prompts and schemas

Each prompt is one `prompts/<name>.md` split into four sections — `SYSTEM`,
`DOCUMENT`, `INSTRUCTIONS`, `TASK` — sent in that order, with the instructions
**after** the document. The template is split before substitution, so a heading
inside an OCR'd document can never be read as a section marker. The `DOCUMENT`
block is `cache_control: ephemeral`.

Large injected content is fenced, and each contract block names its
`contract_id` at both ends so the id the model must quote back is never far from
the lines it is reading:

```
---------- OPINION START ----------
    1│ORDER GRANTING IN PART AND DENYING IN PART ...
---------- OPINION END ----------

---------- CONTRACT 226FSupp3d743_exhibit_b START ----------
    1│PATENT LICENSE AGREEMENT
---------- CONTRACT 226FSupp3d743_exhibit_b END ----------
```

`{clause_def}` is filled from `lib.CLAUSE` in both prompts, so neither can hold a
copy of the clause definition that drifts. Coverage stays step-specific —
step 1 wants the disputed clauses, step 2 wants all of them — but boundaries do
not.

Both steps use structured output (`output_config.format` with a `json_schema`),
so the answer is validated at the API boundary and the model retries on a
mismatch. Schemas live beside their prompts. `additionalProperties: false` and a
complete `required` list are mandatory: without them the model may return a field
the pipeline silently ignores.

| | `extract.schema.json` | `inventory.schema.json` |
|---|---|---|
| per clause | `clause_name`, `taxonomy`, `contract_id`, `start_line`, `end_line`, `head`, `tail`, `opinion_comment_start_line`, `opinion_comment_end_line` | `name`, `start_line`, `end_line`, `head`, `tail` |
| top level | `case_desc`, `clauses` | `clauses` |

Note what is absent from both: **no `text` field**.

---

## 7. Bounds, logging and resumability

Inputs are bounded at **200 tokens** (below this it is a cover sheet or a stamp,
not worth a call) and **900,000 tokens** (1M context less the output budget).
Counts come from `client.messages.count_tokens` — free and exact.

Every call is written to `output/llm_logs/<step>/<call_id>.json`: full system,
document, instructions, task, response and usage. The log is the audit trail. A
refusal and a `max_tokens` truncation are both reported and skipped rather than
crashing the step.

Steps 1 and 2 write their artifact after every call and re-run only what is
missing, so a run can be interrupted and resumed. To redo work already done,
delete the case or contract id from the artifact first. Step 0 and
`build_dataset.py` make no model calls, so the dataset is reproducible from
stored artifacts without an API key.

---

## 8. What this does not catch

Stated plainly, because they are real costs.

**A wide range with correct anchors is undetectable.** If a range starts at the
right clause and ends at the right clause but swallows an intervening one, both
anchors match and the extraction silently contains too much. Three detectors run
in step 2 as flags, rejecting nothing, so which of them earns its place can be
measured rather than assumed:

- clauses out of document order;
- clauses whose character spans overlap;
- a clause longer than `WIDE` × the contract's median.

**Two-column OCR corrupts silently.** Where two columns interleave, anchor
matching accepts the range and extracts both columns interleaved. There is no
cheap detector. It has to be counted by hand and reported, not hidden.

**`clause_name` is free text and is never checked** against what the anchors
locate. A clause named `Section 8` whose anchors land on Section 9 produces a
correctly-extracted clause under a wrong name.

**The opinion passage is range-only and unverified.** It is extracted by
`lib.window` from the reported line numbers with no anchor. It is context, not
label-bearing text — but the asymmetry should be named rather than discovered.

**Step 0 does not stitch.** A contract split across two OCR files is registered
as two documents, and a clause spanning the split cannot be located.

**Clause selection is not reproducible.** The same case run twice can return
different clause sets. Sampling several times and taking a union or a vote is
the obvious answer and is not implemented.

---

## 9. The heuristic register

Every tuned number, in one place. Everything else is a rule, not a threshold.

| number | where | why |
|---|---|---|
| `ANCHOR_WORDS = 8` | `lib.CLAUSE` | long enough to be unique in a contract, short enough to copy off a bad scan without slipping |
| `ANCHOR_MATCH = 0.75` | `locate()` | a model copying 8 damaged words will slip a character; that must not cost a clause |
| `SLACK = 5` | `locate()` | models miscount lines, so the anchor is searched either side of the claimed window |
| `FURNITURE = 20` | `normalise()` | a page number or Bates stamp sits alone on a short line; a clause runs to hundreds of characters |
| `DUP = 0.90` | `step0_corpus` | the same exhibit attached to two filings |
| `WIDE = 6` × median | `step2_inventory` | reporting only — flags a clause long enough to suspect over-capture |
| 200 – 900,000 tokens | `lib.out_of_bounds` | not worth a call / will not fit |

---

## 10. Files

```
prompts/extract.{md,schema.json}      step 1 — locate the disputed clauses
prompts/inventory.{md,schema.json}    step 2 — locate every clause
src/lib.py                            CLAUSE, locate(), normalise(), strip_ocr(), ask()
src/step0_corpus.py                   0. link cases, register documents   (no LLM)
src/step1_extract.py                  1. which clauses were disputed
src/step2_inventory.py                2. every clause of the winners
src/build_dataset.py                     assemble + validate              (no LLM)
src/replay_anchors.py                    score the locator against stored logs

output/cases.json           cases in scope, with keys, headnotes and codes
output/contracts.json       the document registry
output/contracts/<cid>.md   document text, OCR furniture stripped
output/opinions/<id>.txt    opinion text        (gitignored, derived from data/)
output/clauses.json         step 1 — positives, and what was rejected
output/inventory.json       step 2 — every clause of the winners, and the flags
output/llm_logs/<step>/     full prompt, response and usage for every call
output/dataset.csv          the dataset
```

`dataset.csv` columns:

| column | |
|---|---|
| `citation`, `taxonomy`, `key` | the case and the risk type, from the Westlaw key |
| `clause_id` | `pos1…`, `neg1…` within the case |
| `clause_name`, `label` | `POSITIVE` / `NEGATIVE` |
| `provenance` | which step produced the row |
| `case_desc` | one line on the dispute |
| `contract_id`, `contract_file` | which document it was cut from |
| `source_lines`, `source_span` | the line range and the character offsets the anchors snapped to |
| `clause_text` | the normalised extraction — **the dataset text** |
| `anchor_score` | the match quality |
| `opinion_comment` | the passage showing the dispute (positives only) |

`source_span` and `anchor_score` together make every row's provenance
reproducible from the contract file alone, without the model's output.

---

## 11. Open questions

- **`ANCHOR_WORDS = 8`** is a guess. It can be tuned offline against stored logs
  with `src/replay_anchors.py`, at no API cost.
- **Which over-capture detector earns its place** (§8) — all three currently
  report and none rejects.
- **Selection reproducibility** (§8) — several samples per case, with a union or
  a vote, is the obvious fix and is unimplemented.
- **Paragraph structure.** Collapsing a clause to one line loses the shape of a
  numbered sub-list. Worth keeping single newlines between source lines instead?
- **Anchoring the opinion passage** (§8) would be cheap and is not done.

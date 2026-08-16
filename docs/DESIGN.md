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

> **Every other clause of every contract filed in the same case.**

"Other" means every clause whose line window does not intersect a positive's.
That is exact set arithmetic (`lib.overlaps`), not a similarity judgement: a
section containing a disputed sub-clause contains its lines, so it is excluded,
and every exclusion is printed.

### Why negatives come from the same case

A positive's own contract **always** contributes negatives. Those two classes are
cut from the same document, so they are matched on everything except the label:

| matched | so a classifier cannot win by |
|---|---|
| the document | recognising document style or subject matter |
| the OCR condition | recognising scan quality |
| the granularity | recognising clause length |

This matched-condition principle governs several decisions further down, and it
is what makes the design's other trade-offs safe.

The **other agreements of the case** contribute negatives too, and they are 16 of
the 67 contracts — about a fifth of the corpus. A case commonly files several
instruments and the court reaches only some of them: `283 F.Supp.3d 240` filed
four (a replacement policy, the original policy, and two applications) and the
court construed the replacement policy alone. The other three were before the
court and were not construed, which is what a negative is. They are matched on
the case, the parties and the dispute, though not on the document.

They also supply something the winning contracts cannot: **16 documents in which
the right answer is "nothing here"**. An evaluation built only from contracts
that contain a positive cannot see a model that flags something in every
contract it is given, because it never shows one where nothing should be
flagged.

One consequence is worth stating rather than discovering. A negative in a
contract with no positive of its own carries the **case's** taxonomy code rather
than a sibling clause's — still a Westlaw key, never a model's choice, and
unambiguous because every case in scope is filed under exactly one code.

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

**Register.** Write each contract the Contract-Risk repo extracted for those
cases to `output/contracts/<cid>.md`, stripped of OCR furniture, and record it in
`output/contracts.json`. Duplicates — the same document extracted under two
agreement names — are collapsed at `difflib` ratio ≥ `DUP`.

No OCR is run here, and none of it is Datalab's any more. The contract text is
Contract-Risk's, which for each case had already:

| their step | supplies |
|---|---|
| download + `ocrmypdf --force-ocr` | the filing as text |
| `identify_contracts.py` | which file holds which named agreement |
| `extract_contracts.py` | that file cut down to the contract's own lines |
| `verify_contracts.py` → `contract_check.csv` | is it the right contract, and readable |

Their extraction is **verbatim**: the model returns line numbers and their script
cuts the lines, by the same reasoning as §3 here, so no model wrote the words.
Checked independently rather than taken on the README's word — all 205
re-sliceable extractions are byte-identical to their source at the recorded
spans. That is what makes the text admissible as a source for this dataset.

Step 0 registers only the `usable` verdicts unless `--verdict` says otherwise,
and carries their agreement name, source file, line spans, extraction confidence
and verdict onto every registry row, so a clause traces back to the OCR it was
cut from without opening their repo.

**This moves one judgement upstream, and that is a real change.** Which document
is the contract used to be step 1's problem: it was handed a whole bundle of
filings and had to ignore the briefs, cover sheets and unrelated exhibits. Now
that answer arrives with the data. Step 1 is handed contracts — cheaper, better
targeted, and no longer paying input tokens for court filings — but the choice is
theirs, made under their prompt, and a wrong one is not caught here. Their
verdict on every row is the mitigation, not a fix.

The contract id is built from the citation and their agreement name, never from a
name one of our models gave it, and a `taken` set appends a numeric suffix so one
registration can never silently overwrite another.

### Step 0b — `step0b_layout.py` (one cheap call per registered contract)

A screen, not a judgement about the contract, so it does not run on `lib.MODEL`:
Sonnet at low effort, one boolean answer. It asks whether the scan is an
interleaved two-column page — the one corruption nothing downstream can see
(§8) — and it runs before step 1 spends a call on the document.

It is shown windows of consecutive lines spread from the first line of the file
to the last, 25% of the document between a floor of 400 lines and a ceiling of
1,200, with the file's own line numbers. Consecutive, because the signature is
that the same mid-line break recurs line after line and a line on its own proves
nothing; spread out, because a filing can be single-column for eighty pages and
two-column in its endorsements.

It must quote the interleaved lines back verbatim, which grounds the verdict:
every quoted line can be looked up in the file, and on the confirmed cases they
matched exactly. Verdicts go to `output/layout.json` and step 1 drops the
rejected contracts; nothing is deleted and no other step reads the file.

### Step 1 — `step1_extract.py` (one call per case)

Input: the numbered opinion plus every registered document for that case that
step 0b did not reject, each
in its own `---------- CONTRACT <cid> START ----------` block, plus the risk
types the case was selected under and the headnote text.

Output per clause: `clause_name`, `taxonomy`, `contract_id`, the line range, the
two anchors, and the opinion passage showing the dispute. Each is located
(§5) and rejected with a reason if an anchor does not match.

Returning **no clauses is a valid answer** and happens legitimately: the
disputed contract simply was not in what got downloaded.

### Step 2 — `step2_inventory.py` (one call per contract)

This step lists every clause of a contract, in document order. It runs on every
contract step 1 was shown, for every case step 1 processed — 67 of them.

That is two groups. A contract that produced a positive **must** be here, so no
positive is left without negatives from its own document. A contract that
produced none is here because its clauses are negatives in their own right (§1):
it was filed in the case, it was before the court, and the court did not construe
it.

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
**after** the document, so a rule sits next to the text it governs rather than
tens of thousands of tokens above it. The template is split before substitution,
so a heading inside an OCR'd document can never be read as a section marker.

**Nothing is cached.** The `DOCUMENT` block used to carry
`cache_control: ephemeral`, on the reasoning that a second call over the same
document would be charged at cache-read rates. There is no such second call —
step 1 sends an opinion plus every contract of a case, step 2 sends one contract
alone, and no two calls in either step share a prefix. Measured over the first
four real calls: **54,310 tokens written to cache, zero read**. A cache write
costs 1.25× the base input rate, so the marker was a flat 25% surcharge on every
document token for nothing, about $13 over a full run. It is removed. Do not
restore it without a prefix two calls actually share.

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

> **This is no longer hypothetical, and it has been partly addressed.** The
> first end-to-end run found it in 31 of 211 negatives, rising to 75% of clauses
> over 2,000 characters, all in one insurance policy. `lib.CLAUSE` gained the
> standalone test and `prompts/inventory.md` the amendment/definitions shapes;
> after that, merged clauses over 2,000 characters went **3 → 0**, the longest
> merged clause 3,301 → 1,787 chars, and over-capture flags **3 of 5 contracts →
> 0 of 5**. The raw count of mild sibling-runs did not fall (31 → 32); those are
> mostly definitions blocks, where "is each defined term its own clause" is a
> modelling choice rather than a defect.

**OCR duplicates text across a page boundary, and there is no safe detector.**
Where the scan repeats a page's last lines at the top of the next, the anchor
span swallows both copies. Confirmed by hand in `787FSupp2d118` clause `19(e)`.
It is **not repairable by any text rule**, and this was measured rather than
assumed:

- the two copies are not identical — one reads `investment finds managed by`,
  the other `investment funds managed by` — so exact matching cannot find them;
- the obvious discriminator (only page furniture between the copies) has almost
  no precision: over 117 contracts it returns 2,144 candidates, of which the 97
  "clean" ones are nearly all flattened tables (`$ $ $ $`, `wichita wichita
  wichita`). Tightening it leaves 5 candidates in 1 contract, all false
  positives, while still missing the confirmed case.

Catching these would mean deleting contract words on a fuzzy similarity score,
which is the one thing this pipeline must not do. No repair and no flag is
shipped; the honest gap is recorded here instead. The route, if it ever matters,
is page-aware re-extraction from the source PDFs — not a text rule.

**Two-column OCR corrupts silently — now screened out in step 0b.** Where two
columns interleave, anchor matching accepts the range and extracts both columns
run together. Nothing downstream can see it: the anchors match, the line range
is real, and the "clause" is alternating halves of two different clauses. Found
live in `170FSupp3d754_ambit_pennsylvania_northeast_llc` (its clause spans summed
to 140% of the file), then in three more by hand.

A **deterministic** detector was written and measured first, and it is not good
enough. The signature ought to be a gutter — a run of blank space at the same
column position on consecutive lines — and at the top of the ranking it works:
the worst offender scores highest, and 20 contracts that had been through step 2
and read fine score at most 0.08 against its 0.36. But it cannot be made to
catch `741FSupp2d555_oic`, whose left column is often empty so the run breaks;
the obvious repair (treat a line indented to the gutter as continuing the run)
matches every indented sub-clause in the corpus and destroys the separation —
catching all the known cases then costs skipping 28 of 117 contracts, including
two already inventoried and reading fine. Four of the top scorers turned out to
be letterheads and declarations pages.

So the screen is a model call: `step0b_layout.py`, one Sonnet call per
registered contract at low effort, before step 1 spends anything on the
document. The model sees what the rule cannot — that the two halves of the line
are unrelated *text*, which is the actual definition of the fault. It must quote
the interleaved lines back verbatim, which grounds the verdict and makes it
checkable against the file.

Measured on a hand-labelled set of 17: **4 of 4 confirmed two-column caught, 0
false positives among 10 contracts already inventoried and reading fine**, and
the 3 remaining grey-band scorers from the deterministic detector all correctly
passed.

Run over the whole corpus: **6 of 117 rejected (5.1%)**, touching 4 of 68 cases,
3 of which lose every contract they have. 1,500,540 input and 10,401 output
tokens on Sonnet 5 — **$4.66**, against roughly $0.60 for a single step-1 call
on one of the larger cases. Four of the six are insurance policies whose
endorsement pages are two-column, which is the shape the prompt's "on purpose"
section was written for; the other two are the `170 F.Supp.3d 754` disclosure
statements, which are two-column throughout.

Three of the six had already been through steps 1 and 2 before the screen
existed. Their cases, inventories, call logs and 72 dataset rows were deleted;
`layout.json` and the layout call logs were kept, because they are the evidence
for the rejection and the only way a wrong verdict is findable.

The fourth confirmed case came out of the screen's own first run.
`53FSupp3d816` passed while volunteering that it had "occasional two-column
exhibit pages (like GL 0500 amendments) that are legitimate side-by-side clause
layouts". Read by hand, those pages (from line 2773) are plainly interleaved.
The prompt was at fault: most two-column pages in this corpus are *deliberate*
typography — insurance endorsements, policy forms, IRS forms — and nothing told
the model that intent is irrelevant. `prompts/layout.md` now says so in its own
section, and the case is caught.

Nothing is deleted. A rejected contract keeps its file and its registry row;
`output/layout.json` holds the verdict, the evidence and the line counts, and
step 1 drops the contract from the case. A wrong verdict is undone by editing
one boolean.

**`clause_name` is free text and is never checked** against what the anchors
locate. A clause named `Section 8` whose anchors land on Section 9 produces a
correctly-extracted clause under a wrong name.

**The opinion passage is range-only and unverified.** It is extracted by
`lib.window` from the reported line numbers with no anchor. It is context, not
label-bearing text — but the asymmetry should be named rather than discovered.

**Step 0 does not stitch.** A contract split across two OCR files is registered
as two documents, and a clause spanning the split cannot be located.

**Nothing asserts that step 2 enumerated the whole contract.** Every row can
re-cut correctly while the negatives come from only the part of the document the
model got through — a silent sampling bias that `build_dataset.py` cannot see.
Check it by measuring line coverage: what fraction of a contract's non-blank,
non-furniture lines fall inside some inventory clause, and whether the uncovered
runs sit at the edges or in the middle. On the first five contracts coverage ran
72.6%–93.1%, the gaps were almost all under 8 lines and at the front
(declarations pages, item tables), and exactly one middle gap of 8+ lines
appeared — a badly OCR'd rate schedule, which is correctly not a clause. **Re-run
this check after any full run**; a contract dropping to ~40% means step 2 stopped
early.

**Which document is the contract is now Contract-Risk's answer, not ours** (§4).
Their `contract_check.csv` verdict rides on every registry row and only `usable`
is registered by default, but a contract they mis-identified is not caught here,
and a contract they never extracted is invisible to this pipeline no matter how
well the case fits the twelve keys.

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
| `_DOCKET` (a rule, not a number) | `normalise()` | the CM/ECF page header, which the professor's OCR keeps and Datalab's layout model used to drop |
| `DUP = 0.90` | `step0_corpus` | the same exhibit attached to two filings |
| `WIDE = 6` × median | `step2_inventory` | reporting only — flags a clause long enough to suspect over-capture |
| 200 – 900,000 tokens | `lib.out_of_bounds` | not worth a call / will not fit |

### `_DOCKET`, and why a pattern this large is allowed

The professor's OCR keeps the header stamped across the top of every page of a
filing — `Case 2:15-cv-01243-SD Document 1-1 Filed 03/11/15 Page 1 of 20` — where
Datalab's layout model removed it. One lands mid-clause about every 76 lines, so
it has to go, and nothing in `_STAMP` matches it: it is 62 characters, far past
`FURNITURE`.

Every district words it differently and the OCR mangles it further, so no single
spelling is written down. The parts are listed and **any two of them make a
stamp**. `[il1]` in the PageID part is not a typo — OCR reads its capital I as a
lowercase l or a 1, and `PagelD` is the commonest form in this corpus.

What makes it safe is `fullmatch`: the line is dropped only when there is nothing
else on it. One word of contract text sharing the line makes the match fail and
the line survives, stamp and all. Measured over all 282,326 lines of the
extracted corpus before it was adopted:

| | |
|---|---|
| lines dropped | 5,248 (1.86%), of which `_DOCKET` 4,206 |
| dropped lines holding a run of 4 English words | **0** |
| distinct stamp shapes covered | 246 |
| stamps left behind | ~1,000 (0.36%), OCR-mangled past any rule — `Case 2:11-cv-9 Document 3 y HigsiP 9/13 Page 1 of 8` |

Leaving a stamp in is the cheap direction to err; dropping a line the contract
states is the expensive one. The residual is not hidden because it is small.

---

## 10. Files

```
prompts/layout.{md,schema.json}       step 0b — is the scan two-column?
prompts/extract.{md,schema.json}      step 1 — locate the disputed clauses
prompts/inventory.{md,schema.json}    step 2 — locate every clause
src/lib.py                            CLAUSE, locate(), normalise(), strip_ocr(), ask()
src/step0_corpus.py                   0. link cases, register documents   (no LLM)
src/step0b_layout.py                  0b. reject two-column scans         (Sonnet)
src/step1_extract.py                  1. which clauses were disputed
src/step2_inventory.py                2. every clause of every contract
src/build_dataset.py                     assemble + validate              (no LLM)
src/replay_anchors.py                    score the locator against stored logs

data/                       Westlaw headnotes, opinions, linking sheet  (gitignored)
contract_risk/              the Contract-Risk repo's new_approach/      (gitignored)

output/cases.json           cases in scope, with keys, headnotes and codes
output/contracts.json       the registry — text + their agreement/spans/verdict
output/contracts/<cid>.md   contract text, OCR furniture stripped
output/layout.json          step 0b — the two-column verdict and its evidence
output/opinions/<id>.txt    opinion text        (gitignored, derived from data/)
output/clauses.json         step 1 — positives, and what was rejected
output/inventory.json       step 2 — every clause of every contract, and the flags
output/llm_logs/<step>/     full prompt, response and usage for every call
output/dataset.csv          the dataset
```

`dataset.csv` columns:

| column | |
|---|---|
| `citation`, `taxonomy`, `key` | the case and the risk type, from the Westlaw key |
| `clause_id` | `pos1…`, `neg1…` within the CONTRACT, in document order |
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
- **Step 0b's recall is estimated from four confirmed cases**, all of which it
  now catches. Four is not a recall measurement. The screen sees 25% of a long
  document, so a two-column run confined to a few pages of a 10,000-line filing
  could fall between windows; and its own first-round miss was a *reasoning*
  error, not a sampling one. The cheap check on both is `SHARE = 1.0` on a
  sample of contracts it passed, comparing verdicts.

# The dataset pipeline

How `output/dataset.csv` is built, as the code in `src/` actually builds it.

The dataset is a set of **contract provisions labelled risky or not risky**, where
"risky" means *a federal court construed this provision as carrying a specific
construction defect*. The label is never a model's opinion. It comes from the
Westlaw Key Number the case was filed under, and every positive must point at the
passage of the opinion where the court did the construing.

Every model call in this pipeline is **one stateless request to the Claude
Messages API** — no tools, no agent loop, no multi-turn session, no memory
between calls. This is a deliberate constraint: the dataset must not be built by
the same kind of system the dataset is used to evaluate.

---

## 1. The primitive everything rests on

A model cannot be trusted to quote a scanned document. It silently fixes what it
thinks is a typo, drops a line it thinks is noise, and occasionally invents a
clause that ought to exist. But the OCR text is genuinely damaged, so demanding a
byte-exact quote fails on real input.

The resolution is to ask for **two things that must agree**:

| the model returns | what it is for |
|---|---|
| `start_line`, `end_line` | **provenance** — where in the named file this came from |
| `text` | **the boundary and the repair** — the provision, OCR damage fixed |
| `repairs` | one sentence naming what was fixed |

Documents are shown to the model with line numbers (`lib.numbered`):

```
  147│8. INDEMNITY. Carrier shall indemnify and hold harmless Broker
  148│from any and all claims arising out of the performance of this
```

`lib.verify(lines, start, end, text)` then aligns the emitted text against the
words of that window and returns `None` (verified) or the reason it failed:

* words are compared after folding smart quotes, dashes and markdown punctuation,
  and after dropping tokens with no letter or digit in them (a bullet is not a
  word; punctuation *inside* a word is kept, because a comma can decide a case);
* a **substitution** passes only within Levenshtein distance 2 — `Sect1on` →
  `Section` is a repair, a rewritten sentence is not;
* a substitution that **changes a number** is rejected regardless of distance.
  `$1,000` → `$4,000` and `Section 12` → `Section 13` are one-character edits
  that change what the clause means. Digits are compared only when both sides
  have them, so `Sect1on` → `Section` still passes;
* **no word may be added or removed.** A hallucinated clause has no alignment at
  all; `shall not` → `shall` is a deletion and is rejected outright;
* the one exception is **page furniture**: a deletion may cover whole source
  lines if each is ≤ 20 characters and contains no lowercase word of 3+ letters.
  Scanned exhibits carry fax headers (`Jan 15 08 09:36a`), page marks (`p.3`),
  Bates numbers and initials between the lines of a provision. Every word of
  every line touched must fall inside the deletion — a deletion that cuts into a
  line whose remainder was kept is a deletion from a sentence;
* on failure the window is widened once by ±5 lines and retried, because models
  miscount lines and an off-by-few should not cost a clause.

The window is over-inclusive by design (a line holding two subsections belongs to
both), so `_align` first trims it to the stretch the text matches. **The line
window is provenance; the emitted text is the boundary.**

Nothing becomes dataset text by any other route.

### OCR normalisation

`lib.strip_ocr` runs once per file, before anything else — line numbers index its
output, the model sees its output, and `output/contracts/*.md` is written from
it, so a line number can never mean two things. It drops Datalab page markers and
front matter, strips a fixed list of formatting tags (not a blanket `<[^>]*>`,
which would eat `<name@example.com>` — real text in OCR'd correspondence), and
removes figures. Marker writes every figure three times: the markdown, a
paragraph describing the picture, then the alt text repeated verbatim. The
repeated alt text is the anchor — nothing is removed without finding it.

### How a call is made

`lib.ask(name, call_id, effort="medium", **fields)` is the only entry point.

* `prompts/<name>.md` is split into `## SYSTEM` / `## DOCUMENT` / `## TASK`
  **before** substitution, so a heading inside an OCR'd document can never be
  read as a section marker.
* `prompts/<name>.schema.json` is passed as `output_config.format`, so the answer
  is schema-constrained rather than parsed out of prose. The dialect forbids
  `additionalProperties`, `minLength`/`maxLength` and numeric `minimum`/`maximum`;
  every property must appear in `required`.
* The DOCUMENT block is marked `cache_control: ephemeral`, so a second call over
  the same document is charged at cache-read rates.
* Streaming with `get_final_message()`, `max_tokens` 64,000 (thinking included).
* `stop_reason == "refusal"` returns `None` and the step moves on.
* Every call is written to `output/llm_logs/<name>/<call_id>.json` — full system,
  document, task, response, usage. The log is the audit trail.

Inputs are bounded at **200 tokens** (below this it is a cover sheet or a stamp,
not worth a call) and **900,000 tokens** (1M context less the output budget).
Counts come from `client.messages.count_tokens` — Claude's own metering endpoint,
free and exact. tiktoken was measured to undercount Claude by 1.36–1.75× on this
corpus, enough that a tiktoken-derived ceiling rejects files that process fine.

---

## 2. Step 0 — `link_cases.py` (no LLM)

Join three sources and keep the cases worth spending money on:

| input | supplies |
|---|---|
| `data/wl-headnotes-parsed/<key>/citations.csv` | cases filed under each of the 12 taxonomy keys, with headnotes |
| `data/opinions-case-dot-law.csv` | full opinion text |
| `data/Agreements Docket-Opinion Linking Data.xlsx` | whether a contract can actually be downloaded |

A case is in scope when it is under one of the twelve keys, its opinion text is
available, **and** the linking sheet shows at least one Bloomberg entry document.
Doing the acquisition match first is what makes it affordable to hand whole
opinions to a model later instead of regex-harvested snippets.

Writes `output/cases.json` (one entry per case: id, keys, taxonomy codes, entry
document count, bundles downloaded) and `output/opinions/<id>.txt`, which every
later step reads.

### The taxonomy

Westlaw Key Numbers map to six risk types (`lib.KEYS`, `lib.TYPES`):

| code | risk | keys |
|---|---|---|
| 1.1 | lexical ambiguity or vagueness | k143(2), k152, k159 |
| 1.2 | mechanical error (grammar, spelling, punctuation) | k157, k158 |
| 1.3 | general-vs-specific / list scope | k156, k155 |
| 2.1 | conflicting clauses | k162 |
| 2.2 | whole-instrument coherence | k143.5, k147(3), k161 |
| 2.3 | recitals vs operative text | k160 |

Category **1** is an intrinsic defect visible in the clause's own wording;
category **2** arises from the clause's relationship to the rest of the
instrument.

---

## 3. Step A — `step_a_triage.py` (LLM, one call per case × key)

Given the full opinion, the key it was filed under and that key's headnotes: did
the court construe language from the parties' **own instrument**, and is the
dispute the risk type the key denotes?

Returns `usable`, a reason, and the line range of the opinion passage that shows
it. Opinions are clean text from case.law, so there is nothing to repair and
nothing to align — the range only has to be inside the file.

**This step steers download priority only. Nothing from it reaches the dataset**,
and it is not required to build one. The bundles currently on disk were selected
before it was in place, so there is no `output/triage.json` in this repo.

---

## 4. Acquisition and OCR (manual)

Shortlisted bundles are downloaded from Bloomberg Law by hand into
`bloomberg/<citation>_<n>/*.pdf`, then run through Datalab Marker into
`bloomberg_datalab/<citation>_<n>/*.md`. Both directories are gitignored bulk
inputs, provided separately.

A "bundle" is one docket entry's worth of filings — an affidavit with its
exhibits, say. One bundle is many files, most of which are not contracts, and one
instrument may be split across several of them.

---

## 5. Step B — `step_b_survey.py` (LLM, one call per OCR file)

Two questions about one file, answered in a single call because they read the
same text: **is there an instrument in here and where does it begin and end**,
and **what are its provisions, in order**.

Input: the numbered file, the citation it was filed in, and a digest of the
sibling files in the bundle — each one's name, size, first 300 and last 300
characters. The model cannot see their contents, but it can see their edges,
which is what lets it say a file *continues* another. Filename order does not
work: `144 pages.md` sorts before `Declaration Part 2.md`.

Output:

```
instrument_name          "" when the file holds no instrument
instrument_start_line    the instrument's window in this file
instrument_end_line
continues_from           the sibling filename this file continues, or ""
ends_mid_document        true when the instrument runs past this file's end
provisions[]             name, start_line, end_line, text, repairs
note
```

The code then checks, per provision: the window lies inside the instrument's
window; `verify()` passes; and the normalised text has not already been reported.
Anything failing is recorded in `dropped` with its reason rather than silently
discarded.

Court filings are not contracts, so most files legitimately contain no
instrument — of 95 files surveyed, 25 do.

---

## 6. `build_contracts.py` (no LLM)

Three jobs:

1. **Stitch.** Follow `continues_from` to chain files into one instrument. A
   bundle routinely splits a document across files, cut mid-clause.
2. **Cut and rebase.** Write each instrument to `output/contracts/<id>.md` and
   rebase every provision's line numbers onto that new file, so downstream steps
   index the contract rather than the original OCR file.
3. **Deduplicate.** The same instrument is often attached to several filings.
   Two slices matching at `difflib` ratio ≥ 0.90 are the same instrument; the
   copy with more provisions wins, ties broken by length then source filename so
   the outcome does not depend on dictionary order.

The contract id is built from **the citation and the source filename**, never
from the instrument's name — the model words that name differently on every run,
and two instruments in one case can slug to the same four words. A `taken` set
appends a numeric suffix if a base id ever repeats. A full rebuild starts from an
empty registry so ids reproduce exactly; `--case` keeps the other citations.

Writes `output/contracts.json` (the registry: citation, instrument name, file,
sources, char/line counts, provisions with rebased lines).

---

## 7. Step C — `step_c_extract.py` (LLM, one call per case)

The core judgment step. Input is the numbered opinion **plus every registered
instrument for that case**, each tagged with its `contract_id`, plus the risk
types the case was selected under and the headnote text.

The model finds **which provision the court construed**, and returns for each:

```
clause_name
taxonomy                       must be one of the case's own key codes
contract_id                    which supplied instrument
start_line, end_line           the window in that contract
text, repairs                  the provision, repaired
opinion_comment_start_line     where in the opinion the court construes it
opinion_comment_end_line
```

Two invariants make this step safe to run with a model at all:

* **It does not choose the label.** The taxonomy codes come from the Westlaw keys
  and are handed to it as facts; a code outside the case's own set is rejected.
* **Every clause must point at the opinion passage** where the court construes
  it. That anchors clause selection to the court's own words rather than to what
  looks risky to a model of the same family as the ones under evaluation.

Both windows are checked — the clause window through `verify()`, the opinion
window for being in range — and failures are recorded in `rejected`.

Returning **no clauses is a valid answer** and happens legitimately: the
construed contract simply was not in what got downloaded. Three of ten cases are
such nulls.

The size ceiling applies to the whole call (opinion + all instruments), measured
from stored artifacts before the call is made.

---

## 8. `build_dataset.py` (no LLM)

**Positives** are the clauses step C found.

**Negatives** are every *other* provision of the instrument those positives came
from — "other" meaning every provision whose line window does not intersect a
positive's. That is exact set arithmetic, not a similarity score: the section
containing the disputed subsection contains its lines, so it is excluded and the
exclusion is printed.

Negatives come from the same instrument as the positives on purpose. The two
classes are then the same document, in the same OCR condition, drafted by the
same parties — so a classifier cannot win by recognising document style.

Then it validates, loudly, and refuses to write on failure:

* no two rows share a `clause_text`;
* every row re-verifies against its contract file (`verify()` again, from disk);
* every positive has a non-empty `opinion_comment`.

### `dataset.csv`

| column | |
|---|---|
| `citation`, `taxonomy`, `key` | the case and the risk type, from the Westlaw key |
| `clause_id` | `pos1…`, `neg1…` within the case |
| `clause_name`, `label` | `POSITIVE` / `NEGATIVE` |
| `provenance` | `step C — construed by the court` / `step B — not disputed` |
| `case_desc` | one-paragraph description of the dispute |
| `contract_id`, `contract_file`, `source_lines` | provenance |
| `clause_text` | the repaired text — **the dataset text** |
| `clause_text_raw` | the raw source window |
| `clause_repairs` | what was fixed |
| `opinion_comment` | the court's own words (positives only) |

Carrying both the repaired and the raw text is what makes every repair auditable,
and lets the whole dataset be regenerated from the raw column alone.

---

## 9. The heuristic register

Every tuned number in the pipeline, in one place. Everything else is a rule, not
a threshold.

| number | where | why |
|---|---|---|
| Levenshtein ≤ 2 per substituted run; no inserts/deletes; no number changes | `verify()` | separates OCR repair from rewriting |
| ±5 lines, retried once | `verify()` | models miscount lines |
| ≤ 20 chars and no lowercase 3+ letter word = page furniture | `verify()` | fax headers and Bates stamps sit between the lines of a provision |
| 0.90 similarity = duplicate instrument | `build_contracts` | the same exhibit attached twice |
| 200 – 900,000 tokens | `lib.out_of_bounds` | not worth a call / will not fit |
| 300 characters per sibling edge | `step_b_survey` | enough to recognise a continuation |

---

## 10. What it has produced

| | |
|---|---|
| cases under the 12 keys with opinions and downloadable contracts | 475 |
| bundles downloaded and OCR'd | 39, for 32 cases |
| cases run end to end | 10 |
| files surveyed (step B) | 95 → 25 contain an instrument |
| provisions verified / dropped | 719 / 7 (**1.0 %**) |
| instruments registered | 20, one stitched from 2 files |
| clauses extracted (step C) | 14 kept, 0 rejected, 3 correct nulls |
| **dataset.csv** | **242 rows — 14 positive, 228 negative, 7 contracts** |
| repaired words | 288 of 29,327 (**0.98 %**) |
| model calls | 105 |

Positives by type: 1.1 = 2, 1.3 = 4, 2.1 = 3, 2.2 = 5. **Types 1.2 and 2.3 have
no positives** — no case filed under 1.2's keys has been downloaded, and 2.3's
one downloaded case was a null.

Steps A, B and C are resumable: each writes its artifact after every call and
re-runs only what is missing. `build_contracts.py` and `build_dataset.py` make no
model calls at all, so the dataset is reproducible from stored artifacts without
an API key.

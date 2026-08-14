# Migrating the corpus to the Stanford contract text

The pipeline was built against contract text produced in-house: bundles
downloaded from Bloomberg Law as PDF, run through Datalab Marker into markdown
under `bloomberg_datalab/`, then filtered by an LLM survey that decided per file
whether it held a contract at all.

That route is retired. The Stanford repo already contains contract text that has
been identified, cut and quality-checked, so there is nothing left for OCR or for
a survey step to decide. **Step 0 changes to read that text; steps 1 and 2 and
`build_dataset.py` do not change at all.**

The two corpora do not overlap. The ten cases currently in `output/` came from
the Datalab route and are a closed batch — finish them or drop them, but nothing
more is coming for them. Everything below is about the corpus that replaces them.

---

## 1. What is needed, and where it is

Source repo, referred to below as `$STANFORD`:

```
~/Downloads/Contract-Risk-Stanford
```

### 1.1 Present and usable right now

| path (under `$STANFORD/new_approach/`) | what it is |
|---|---|
| `generated/corpus/contracts/extracted_contracts/*.txt` | **209 contract text files**, one per contract, named `<case_slug>__<agreement_slug>.txt` |
| `generated/corpus/contracts/contract_extraction.csv` | 209 rows — maps each file to its case citation and agreement name |
| `generated/corpus/contracts/contract_check.csv` | 209 rows — a `verdict` per file, the quality gate |
| `generated/corpus/contracts/contract_files.csv` | 274 rows — which source PDF each contract came from |
| `generated/acquisition/citation_to_casename.json` | 150 citations → case names |

`contract_extraction.csv` is the index to build from. Its columns:

```
case_citation      "118 F.Supp.3d 802"  — already in the display form cases.json uses
agreement          "Membership Agreement"
source_file        contracts/118_f_supp_3d_802/entry_1/Ex A.pdf
extracted_file     generated/corpus/contracts/extracted_contracts/118_f_supp_3d_802__membership_agreement.txt
spans              "1-885"
n_lines_total, n_lines_kept, n_chars_in, n_chars_out
confidence         high | medium | low
note
```

**Do not parse the filenames.** `case_citation` is carried in the CSV in exactly
the form `cases.json` is keyed by, so the join is direct and there is no slug
round-trip to get wrong.

### 1.2 Still needed — Git LFS pointers, not yet pulled

Every file in `$STANFORD/inputs/after_july2025/` is a 130-byte LFS stub. Run
`git lfs pull` in that repo to materialise them.

| file | real size | needed for |
|---|---|---|
| `opinions-case-dot-law.csv.zip` | **142 MB** | **Blocking.** Step 1 cannot run without opinion text |
| `Agreements Docket-Opinion Linking Data.xlsx` | 2 MB | not needed — acquisition is already done |
| `merged.xlsx` | 36 MB | not needed |
| `stanford_case_citations_user.xlsx` | 2 MB | not needed |

Only the opinions file blocks the pipeline. Unzip it to
`data/opinions-case-dot-law.csv`; it needs the columns `citation`, `text` and
(optionally) `case_name`, which is what the current linking code already reads.

### 1.3 Not needed any more

- `bloomberg_datalab/` — retired with the Datalab route.
- `bloomberg/` — the source PDFs. The Stanford repo has its own copies under
  `new_approach/contracts/<case>/entry_<n>/`, and nothing reads them.
- `data/wl-headnotes-parsed/` — **already covered**. `output/cases.json` holds
  the keys, taxonomy codes and headnote text for all 475 cases, so the headnote
  join does not have to be redone. Keep that file.

---

## 2. What the corpus will be

Joining `contract_extraction.csv` against `output/cases.json`, and keeping only
files `contract_check.csv` marks `usable`:

| | |
|---|---|
| extracted contracts | 209 |
| whose `case_citation` is in `cases.json` | 143 |
| of those, `verdict == usable` | **123** |
| distinct cases | **68** |
| risk types | 1.1 = 59 cases, 2.2 = 27, 1.3 = 7 |
| contract size | 2,265 – 690,677 chars, median 49,975 |

`contract_check.csv` verdicts across all 209: `usable` 171,
`empty_or_unreadable` 26, `partial` 4, `wrong_doc_type` 4, `wrong_contract` 4.

Two things follow.

**Filter on `verdict == usable`.** The 38 non-usable files are a checked
judgement about the extraction, not about the law, and there is no reason to
spend step-1 tokens on text already known to be empty or the wrong document.
Record the count that was dropped; do not drop it silently.

**No risk type 1.2, 2.1 or 2.3.** The corpus is heavily 1.1. That is a property
of what was acquired, not of the pipeline, and it should be stated wherever the
dataset's coverage is reported.

The largest contract is 690,677 chars ≈ 185k tokens, comfortably inside the
900k ceiling even with an opinion attached, so no case should be skipped for
size. Verify with `lib.out_of_bounds` rather than assuming.

---

## 3. How to change step 0

Only `src/step0_corpus.py` changes. Its two jobs stay the same — **link** cases,
then **register** documents — and the second one gets a new source.

### 3.1 `link()` — one line

`headnote_cases()`, `entry_documents()` and the case-scope rule are all
unnecessary now: acquisition already happened, and `cases.json` already holds the
headnotes. Replace the three-way join with:

- read the existing `output/cases.json` for keys, taxonomy and headnotes;
- read `data/opinions-case-dot-law.csv` and write `output/opinions/<id>.txt` for
  every case that has a contract in the new registry;
- drop `bundles()` entirely — there are no bundles.

Keep `--skip-link` so a re-register can run without re-reading a 149 MB CSV.

### 3.2 `register()` — read the CSV, not a glob

Replace `candidates()`, which globs `bloomberg_datalab/<bundle>/*.md`, with a
reader over `contract_extraction.csv`:

```python
def candidates(citation, rows):
    """The usable extracted contracts for one citation."""
    out = []
    for r in rows:                          # rows already filtered to this citation
        if r["extracted_file"] not in USABLE:      # from contract_check.csv
            continue
        path = STANFORD / "new_approach" / r["extracted_file"]
        text = strip_pdf_text(path.read_text(encoding="utf-8", errors="replace"))
        if len(text) < lib.MIN_INPUT_TOKENS:
            continue
        out.append({"source": r["extracted_file"], "agreement": r["agreement"],
                    "text": text, "norm": " ".join(text.split()).lower()})
    return out
```

Everything downstream of that is unchanged: the `DUP = 0.90` dedupe, the
`contract_id` builder with its `taken` set, writing `output/contracts/<cid>.md`,
and the registry entry. Two small notes:

- Build the id from the **agreement name** now rather than the source filename —
  `lib.slug(r["agreement"])` gives `118FSupp3d802_membership_agreement`, which is
  stable because it comes from the CSV rather than from a model. The `taken` set
  still guards collisions.
- Add `agreement` and `confidence` to the registry entry. They cost nothing and
  make a row traceable back to the Stanford CSV.

### 3.3 `strip_ocr()` — needs a plain-text sibling

**This is the one real code change beyond plumbing.** `lib.strip_ocr` was written
for Datalab markdown: it drops `----- Page N -----` markers, markdown image
syntax and HTML formatting tags. The Stanford files are **plain text extracted
from PDF**, not markdown, and carry different furniture. Measured over the first
40 files:

| pattern | occurrences |
|---|---|
| `Case 2:15-cv-01243-SD Document 1-1 Filed 03/11/15 Page 1 of 20` | 820 |
| Bates-like stamp (`ABC 001234`) | 319 |
| bare page number on its own line | 236 |
| `Page 3 of 20` | 181 |

They also contain form feeds (`\x0c`) and long runs of blank lines. Only 17 of
40 contained any markdown-ish marker at all, and those were incidental.

Write a `strip_pdf_text()` beside `strip_ocr()` rather than extending it — two
input formats, two functions, so neither drifts. It must obey the same rule
`strip_ocr` obeys:

> **Blank lines, never remove them.** Line numbers must index the same text the
> model sees and the same text `output/contracts/<cid>.md` is written from, or a
> line number means two different things.

What it should blank:

- the ECF header line — anchor it tightly, e.g.
  `^\s*Case \d+:\d+-[a-z]{2}-\d+.*Document .* Filed .* Page \d+ of \d+\s*$`;
- a bare `Page X of Y` line;
- form feeds (replace with a space, preserving offsets).

What it must **not** touch: bare page numbers and Bates stamps. Those are already
handled downstream by `lib.normalise()`, which drops them from the extracted span
only — deliberately, so the contract file keeps them and `source_span` offsets
stay meaningful. Blanking them at step 0 would delete a figure the contract
states. See DESIGN.md §5.

### 3.4 What does not change

`step1_extract.py`, `step2_inventory.py`, `build_dataset.py`, both prompts,
`lib.CLAUSE`, `lib.locate()` and `lib.normalise()` are all untouched. They read
`contracts.json` and `output/contracts/<cid>.md`, and neither changes shape.

---

## 4. Order of work when the opinions land

1. `git lfs pull` in `$STANFORD`; unzip to `data/opinions-case-dot-law.csv`.
2. Archive the current run — `output/` holds a finished pilot on a corpus that is
   being retired. Move it aside rather than letting a rebuild interleave with it;
   `contracts.json` changes format and the ids change, so the old artifacts are
   not a valid starting point.
3. Rewrite step 0 as above. It needs no API key, so verify it fully offline:
   123 contracts registered, 68 cases, ids unique, every `contracts/<cid>.md`
   re-readable, and `strip_pdf_text` leaving the line count unchanged.
4. Run `src/replay_anchors.py --logs <archived logs>` once to confirm the locator
   still scores as before on text from the new source.
5. Run step 1 on two or three cases first and read the output before committing
   to the full 68.

---

## 5. Open questions

- **`contract_files.csv` has 274 rows against `contract_extraction.csv`'s 209.**
  The extra 65 are contracts that were identified but not extracted. Worth
  knowing whether they were dropped deliberately or simply never processed,
  because they are potential corpus.
- **`confidence` is unused** in the plan above; only `verdict` gates. If `low`
  confidence correlates with bad extractions the gate could tighten, but that
  should be measured on the 26 `low` rows rather than assumed.
- **Opinion coverage is unverified.** 68 cases need opinion text; the join has
  not been run because the CSV is still an LFS stub. Check it before planning a
  full run — a case with a contract but no opinion cannot go through step 1.
- **The taxonomy is lopsided** (59 of 68 cases are 1.1). Whether to rebalance by
  acquiring more 2.x cases is an acquisition question, not a pipeline one.

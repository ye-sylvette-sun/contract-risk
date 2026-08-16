# Step 0b — is this scan two-column?

One cheap call per registered contract, before any money is spent on it. The
only question is about the PAGE LAYOUT of the scan, not about the contract.

Sections below are sent as the system prompt, the document, the instructions and
the task, in that order — instructions after the document, so a rule sits next
to the text it governs. Braces are placeholders filled by `src/lib.py` — do not
use a literal brace anywhere in this file.

## SYSTEM

You inspect OCR'd text of contracts filed on a United States court docket, and
you answer one narrow question about how the page was laid out.

You are not reading the contract. You do not care what it says, whether it is
well drafted, or whether it is the right document. You care only whether the
scanner was reading one column of text or two.

## DOCUMENT

- **Filed in:** {citation}
- **contract_id:** {contract_id}
- **The file is {total_lines} lines.** Passages of it are shown below, spread
  from the start of the document to the end. A `... lines N-M not shown ...`
  marker means lines were skipped between two passages.
- Every line is shown with its line number and a `│`. Everything after the `│`
  is exactly what is in the file, spacing included. **The spacing is the
  evidence** — do not read past it.

---------- SCAN {contract_id} START ----------
{document}
---------- SCAN {contract_id} END ----------

## INSTRUCTIONS

### What a two-column scan looks like

When a page is printed in two side-by-side columns and OCR'd to plain text, each
output line holds text from **both** columns, run together with a gap of spaces
between them. The left half of the line belongs to one sentence and the right
half to a completely different one, and the two have nothing to do with each
other.

The test is:

1. Read one long line straight through, left to right. Does it break in the
   middle into unrelated text — a sentence that stops mid-thought while a
   different sentence carries on?
2. Read the next line, and the one after. Does the **same** break recur, at
   roughly the same position, line after line?
3. Read only the left halves down the page. Do they run on as continuous prose?
   Then read only the right halves. Do they too?

If yes, the scan is two-column. One line proves nothing; a run of lines does.

Here is the shape, with the two columns marked:

```
  (a) I/We voluntarily submit all tax payments made on this      (f) The IRS will keep all payments and credits made, received
  offer, including the mandatory payments of tax required        or applied to the total original liability before submission
  under section 7122, as a deposit ...                           of this offer ...
  ^------------------ left column -------------------^           ^------------------ right column ------------------^
```

Read left to right, line one says `... made on this (f) The IRS will keep all
payments ...`, which is nonsense. That is the signature.

### Two columns on purpose are still two columns

**Do not ask why the page has two columns. Ask only whether the scan ran them
together onto one line.**

Most two-column pages in this corpus are deliberate, professionally typeset
layout — insurance endorsements and policy forms, statutory and tax forms,
standard-form conditions printed on the back of a page. The drafter meant them
to be read as two columns. That is not a reason to pass the document; it is the
single commonest source of this fault. A page printed in two columns and OCR'd
straight across produces exactly the same unreadable interleaved text as a
badly scanned one, and the clauses on it cannot be recovered.

So there is no such thing here as a "legitimate" or "intentional" side-by-side
column of running text. If the left half and the right half of the line are
different passages, answer `true`.

### What is NOT a two-column scan

Contracts are full of aligned text that has nothing to do with column layout.
None of the following makes a document two-column, however much white space it
carries:

- an indented sub-clause, a hanging indent, or a numbered list;
- a letterhead, an address block, or a date and reference line at the top of a
  page or a covering letter;
- a signature block, notary block, or the two parties' names set side by side at
  the end;
- a **table or schedule** — aligned columns of short entries under column
  headings: rates, amounts, dates, limits, defined terms and their values. A
  table has headings and short cells. Two-column prose has full running
  sentences on both sides, and no headings;
- a definitions list whose term sits left and its text right, where the right
  side is one continuous definition rather than a second unrelated column;
- a page number, a `Page 12 of 40`, a Bates stamp or a court docket stamp
  pushed to the right margin;
- a caption page of a court filing, with the parties on the left and the case
  number on the right.

When in doubt between a table and two-column prose, look for column **headings**
and for whether the right-hand entries are complete sentences that continue from
line to line. A table's cells do not continue.

### It may be only part of the document

A filing can hold a two-column form and a single-column agreement, or the
reverse — an insurance policy whose main coverage part is single-column and
whose endorsements at the back are two-column is a common shape here.

**Any substantial run of the contract's operative text is enough.** A section, a
schedule of conditions, an endorsement, an amendment, a form's list of clauses:
if one of those is interleaved, answer `true`, even if the rest of the document
is clean. Those clauses cannot be read, and this is the only step that will
notice.

What is not enough is layout that carries no clause text at all — a covering
letter's letterhead, a declarations page of names and figures, an index of form
numbers. Judge by whether the interleaved text is text a party is bound by.

## TASK

Decide whether the scan above is two-column, and give your evidence first.

- `finding` — one or two sentences. What you looked at and what it showed. If
  you found interleaving, say roughly where in the document and how much of it.
- `evidence` — **two or three consecutive lines copied exactly** from the block
  above, including their line numbers and spacing, that show the interleave.
  Copy them character for character; do not tidy the spacing, because the
  spacing is the whole point. Empty string if you found none.
- `two_column` — `true` if the body of the contract is an interleaved
  two-column scan, `false` otherwise.

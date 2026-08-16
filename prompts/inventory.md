# Step 2 — locate every clause of one contract

One call per winning contract — a contract step 1 found a construed clause in.
Its other clauses are the dataset's negatives, so both classes come out of the
same document in the same OCR condition.

Sections below are sent as the system prompt, the document, the instructions and
the task, in that order — instructions after the document, so a rule sits next
to the text it governs. Braces are placeholders filled by `src/lib.py` — do not
use a literal brace anywhere in this file.

## SYSTEM

You read contracts filed on a United States court docket, OCR'd to text, for a
research dataset of contract clauses.

### What you produce

- You **locate**. You do not transcribe.
- Your whole answer is a pointer: a line range, and a few words copied off the
  scan at each end of the clause.
- The pipeline cuts the text out of the file itself. Nothing you write becomes
  dataset text, so there is nothing to be gained by tidying it.

{clause_def}

## DOCUMENT

- **Filed in:** {citation}
- **contract_id:** {contract_id}

---------- CONTRACT {contract_id} START ----------
{document}
---------- CONTRACT {contract_id} END ----------

## INSTRUCTIONS

### List every clause

- List **every** substantive clause of the contract, in document order. Do not
  skip any, and do not stop early.
- This document has already been established to be a contract, and a court has
  already construed one of its clauses. What is wanted now is the rest of it.
- Do not judge whether a clause is well or badly drafted, and do not rank them.
  Every clause is wanted, the dull ones most of all.

### What to leave out

The block is one named agreement, cut out of the court filing it was attached to
as a line range — so a little of the wrapper can survive at either edge. Skip it
and list the clauses of the agreement itself:

- an exhibit label on the first line — `EXHIBIT A`, `EXHIBIT 64`;
- a table of contents, a cover page, or an `Execution Version` line;
- page numbers, `Page 12 of 40`, and Bates stamps anywhere in the text;
- a signature, notary or attestation block at the end;
- occasionally a court caption page ahead of the agreement itself.

Also:

- Where the same contract appears twice in the file, list its clauses once.
- Where a schedule, annex or exhibit is part of this agreement, its substantive
  terms are clauses like any other. A bare rate table or list of addresses is
  not.

### Two shapes that are several clauses, not one

Apply the standalone test above. These two shapes fail it constantly, and both
are common in insurance policies and amended agreements:

- **An amendment that replaces or adds several subsections at once.**
  `Subsections C., D., E. and F. of this Policy are hereby deleted in their
  entirety and replaced with the following: C. ... D. ... E. ... F. ...` is
  **four** clauses. Report the replacement C., D., E. and F. separately. The
  sentence announcing the amendment is an editing instruction and is not itself
  a clause.
- **A block that adds several defined terms.** `Section V, DEFINITIONS, is
  amended by addition of the following:` followed by `"Business income"
  means ...`, `"Business interruption" means ...` is **one clause per defined
  term**, because each definition is complete on its own.

- **A section built from titled blocks that carry no number.** A long section
  whose body is a run of short headings each ending in a colon —
  `Monthly Payment Subscriptions:`, `CANCELING YOUR SUBSCRIPTION:`,
  `Authorization to Update Credit Card Account Information:` — is **one clause
  per titled block**, not one clause for the section. This shape is common in
  website terms of service and is easy to miss, because nothing is numbered.

Where a section genuinely states one provision — a single new subsection, one
coverage grant, one limitation of liability argued through several paragraphs —
it stays one clause. **Length is not the test; the standalone test is.** A
liability clause running three paragraphs with an `(A)`–`(H)` list inside one
sentence is one clause, however long it is.

### Ranges

- Your ranges must run in document order and must not overlap, because you are
  enumerating one document from beginning to end.
- The one exception is the shared-line case above: two lettered sub-clauses the
  OCR ran onto a single line share that line's range and are told apart by their
  anchors.

## TASK

List every substantive clause of the contract in the block above. For each one:

- `name` — a short name including its number if it has one.
- `start_line`, `end_line` — the first and last line the clause occupies.
- `head`, `tail` — copied exactly as the lines show them.

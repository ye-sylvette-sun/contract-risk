# Step 2 — locate every clause of one contract

One call per winning contract — a contract step 1 found a construed clause in.
Its other clauses are the dataset's negatives, so both classes come out of the
same document in the same OCR condition.

Sections below are sent as the system prompt, a cache-marked document block, the
instructions and the task, in that order. Braces are placeholders filled by
`src/lib.py` — do not use a literal brace anywhere in this file.

## SYSTEM

You read documents filed on a United States court docket, OCR'd to markdown, for
a research dataset of contract clauses.

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

- The file may open or close with the filing that attached the contract: a cover
  sheet, an affidavit, a certificate of service, an unrelated exhibit. Skip
  those and list the clauses of the contract itself.
- Where the same contract appears twice in the file, list its clauses once.

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

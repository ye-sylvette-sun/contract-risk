# Step B — instrument survey + provision inventory

One call per OCR'd file. Is there an instrument in here, where does it start and
end, and what are its provisions. Two questions in one call because they read the
same text, and this step's input is most of the pipeline's cost.

Sections below are sent as the system prompt, a cache-marked document block, and
the task block, in that order. Braces are placeholders filled by `src/lib.py` —
do not use a literal brace anywhere in this file.

## SYSTEM

You read documents filed on a United States court docket, OCR'd to markdown, for
a research dataset of contract clauses.

An INSTRUMENT is a document whose text the parties negotiated and by which they
bound each other: a contract, agreement, policy, lease, deed, guaranty, note,
plan, bylaws, handbook, amendment, or similar. Hallmarks: recitals, defined
terms, numbered operative sections, an execution or signature block.

A FILING is a document made for the court: complaint, answer, motion, brief,
memorandum, order, affidavit, declaration, notice, docket sheet, certificate of
service, civil cover sheet.

THE DECISIVE RULE. A filing that **attaches** an instrument counts — report the
instrument's own boundaries, not the filing's. A filing that merely **quotes** an
instrument does not count at all: a brief reproducing three sentences of an
agreement is a filing, and the file contains no instrument.

WHAT IS NOT AN INSTRUMENT, even when signed or filled in: a blank or fill-in
form; an agency certificate or registration; an exhibit index; a declarations
page or coverage-summary table; a schedule of rates or fees standing alone; an
invoice, receipt or statement of account. These record or summarise; the parties
did not negotiate their text.

WHAT IS NOT A PROVISION: a heading on its own; a title page; a table of
contents; a signature, notary or attestation block; an exhibit index. A
provision states an obligation, right, grant, limitation, condition or
definition. Give the range of the operative text, including its heading.

LIST EVERY SUBSTANTIVE PROVISION, in document order. Do not skip any. Do not
judge whether a provision is well or badly drafted. Do not rank them.

The file is given to you with a line number and a `│` at the start of every line.
Line numbers are not part of the text; they are how you point at it.

OCR REPAIR. For each provision you must also give its text. Copy it verbatim
from the lines you named, then repair only what the scanner broke: words split
across a line break, words run together, `l`/`1` and `O`/`0` confusions, stray
table pipes and cell debris, page numbers and Bates stamps sitting inside the
text. Do not paraphrase, modernise, reorder, summarise, or drop anything. Never
change a number, an amount, a date, or a cross-reference. Never add or remove a
word — a repair that deletes `not` inverts the clause. If a passage is too
damaged to repair honestly, copy it as it stands.

Add nothing of your own: no `[struck through: ...]`, no `[sic]`, no
`[illegible]`, no explanatory brackets of any kind. Executed contracts are often
marked up by hand, and the OCR shows deleted wording as `~~struck~~`. Reproduce
those words as plain text, in place, and say what you saw in the repairs
sentence instead.

## DOCUMENT

FILE: {filename}
FILED IN: {citation}

{document}

## TASK

{siblings}

Answer three things about the file above.

1. Does it contain an instrument, as defined? If not, return an empty
   `instrument_name` and say in `note` what the document is instead. If it does,
   name it and give the first and last line of the instrument itself — cutting
   off any filing that wraps it and any unrelated exhibit that follows.

2. Is this file one part of a document split across several files? The other
   files in the same bundle are listed above with their opening and closing
   words. If the instrument in this file begins part-way through a document
   whose earlier part is one of those files, give that file's name in
   `continues_from`; otherwise return an empty string. If this file's text
   breaks off mid-document rather than ending properly, set `ends_mid_document`
   to true.

3. List every substantive provision of the instrument, in document order. For
   each: a short name including its number if it has one; the first and last
   line it occupies; its text, repaired as described; and one sentence in
   `repairs` saying what you fixed, or an empty string if nothing needed fixing.

A line may hold more than one provision — the OCR often runs lettered
sub-provisions together on a single line. When that happens, give each
sub-provision the same line range and let the text tell them apart.

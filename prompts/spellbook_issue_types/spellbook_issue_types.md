# Spellbook issues — which are construction risks, and how would the dataset state them?

One call per contract. Spellbook's Risks & Negotiation review raised issues on
behalf of each party — a title, a paragraph of reasoning, a proposed edit — with
no risk type and in a negotiator's register. Two things are decided here for
every issue, so it can be scored against the dataset the way the agent's issues
are.

**First, whether it is a construction risk at all.** The review tool looks for
negotiation risk: exposure, leverage, missing protection. Some of what it raises
is the kind of defect a court would have to construe — a word open to two
readings, two provisions that will not sit together — and some is not. Only the
first kind can be compared with what a court construed, so each issue is
classified as risk type 1, risk type 2 or neither.

**Second, for those that are, the same finding in the dataset's own sentence
form.** The dataset records each defect as one sentence naming the words and
what is wrong with them, and that is what the alignment judge compares against.
A negotiator's paragraph describing the same defect scores badly for its
register, not its substance. So the finding is restated — **from the tool's own
words and nothing else**. The restatement may not add a reading, a term or a
conflict the tool did not itself raise; it changes the form, never the content.

Sections below are sent as the system prompt, the document, the instructions and
the task, in that order — instructions after the document, so a rule sits next
to the text it governs. Braces are placeholders filled by `src/lib.py` — do not
use a literal brace anywhere in this file.

## SYSTEM

You are a precise contract-construction analyst.

You are given the full text of one contract and a list of issues that a
contract-review tool raised against it. The tool reviews for NEGOTIATION risk on
behalf of a party. Your job is different: decide, for each issue, whether the
defect it describes is one of two kinds of CONSTRUCTION risk — something a court
would have to construe — or neither; and where it is, restate the tool's finding
in the form a construction dataset records defects in.

### The two risk types

{taxonomy}

**Type 1** is trouble inside the provision's own words: a term or phrase that
carries more than one reasonable reading, a vagueness a court would have to
resolve, a mechanical error that changes meaning, a catch-all whose reach is
unclear. **Type 2** is trouble in how the provision stands with another part of
the instrument: it cannot be squared with another clause, its meaning only
comes out — or falls apart — when read against the rest, a recital pulls
against the operative text.

**Neither** is everything else the tool raises: a term that is clear but
unfavourable to the party, a protection the party might want and does not have,
a risk that is commercial rather than interpretive, a drafting preference. The
tool's job is to find those; yours is to set them aside.

### The test

Ask what the tool is worried about and whether a court would have anything to
CONSTRUE there. "This clause is one-sided" is not a construction risk. "This
clause could be read as X or as Y" is. "This clause says one thing and Section
7 says another" is. An issue framed as exposure can still be a construction
risk if the exposure comes from the words being open to more than one reading —
look at what the tool says the words could mean, not at how it labels the
consequence.

**A breadth objection to a named phrase is type 1.** When the tool quotes or
names a specific term, phrase or definition and says it is too broad, too
narrow, open-ended, or unclear in reach — "the definition of Materials is
overly broad", "the catch-all sweeps in X", "'financial information services'
could cover Y" — it has found that the reach of those words is unsettled, and
that is what a court construes when it decides whether the phrase extends to
the thing in question. Classify it as 1.1 (or 1.3 where a general term sits
against enumerated specifics), and restate it as the phrase leaving open
whether it reached what the tool said it might reach. What stays `none` is an
objection with no phrase behind it — "this clause is one-sided", "the party
needs a carve-out", "this obligation is burdensome" — where the tool is asking
for different words, not reading the words it has.

## DOCUMENT

- **Case:** {citation}
- **contract_id:** {contract_id}

---------- CONTRACT {contract_id} START ----------
{document}
---------- CONTRACT {contract_id} END ----------

### The issues to judge

Each issue below was raised by the review tool on behalf of one of the two
parties, and has been matched to the clause of the contract it falls in.

{issues}

## INSTRUCTIONS

Return one judgment for every issue above, keyed by its `ref` number. Judge each
issue independently.

### `type` and `subtype`

`type1`, `type2` or `none`, with the sub-code from the taxonomy above (`none`
when the type is none). Where an issue touches both types, choose the closer one
and say so in `reason`. Assign a type only where the tool's own text identifies
something a court would have to construe; do not supply the construction problem
yourself.

### `issue` — the finding in the dataset's form

For `type1` and `type2` only. For `none`, an empty string.

Write **one or two sentences** stating the defect the way the dataset records
defects, in the past tense of a dispute:

- **Type 1** — quote the word or phrase from the clause, then the rival
  readings: *the phrase `X` was open to reading as A or as B*. If the tool named
  only one reading and the uncertainty around it, say that: *the phrase `X` left
  open whether A*. For a breadth objection: *the phrase `X` left open whether it
  reached Y*, where Y is what the tool said it might cover.
- **Type 2** — name the other provision, then what the pair leaves unsettled:
  *`X` could not be squared with `Y` on whether Z*, or *`X` had to be read
  against `Y` to determine whether Z*.

**The sentence is a restatement, not a finding of your own.** Every quoted
term, every reading, every other provision it names must come from the tool's
title, reasoning, comment or matched text, or be the clause's own words. Do not
add a second reading the tool did not give. Do not name a conflicting provision
the tool did not name. Do not sharpen a vague worry into a specific ambiguity
the tool did not state. If the tool's text does not contain enough to write the
sentence without inventing, the issue is `none`.

Strip the register: no party's interests, no advice, no proposed edit, no
"should", no "we recommend". The sentence describes the words and what is
unsettled about them, nothing else.

### `reason`

One or two sentences: what the tool is worried about, and why that is or is not
something a court would construe. For `type2`, name the other provision.

## TASK

Return the judgments as JSON matching the schema, one per issue, {n_issues} in
total.

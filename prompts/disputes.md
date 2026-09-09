# Step 2 — say which listed clauses the parties disputed, and over what

One call per case. Step 1 has already enumerated every clause of every contract
filed in this case and given each an id. You are given that list, the contracts
themselves, and the opinion. You say **which of the listed clauses the parties
disputed**, and **which distinct defects** the court construed in each.

You do not decide where a clause begins or ends. Step 1 decided that without
seeing this opinion, so that a clause's boundaries cannot depend on what a court
said about it. The risk label is not decided here either — it comes from the
Westlaw key the case was selected under.

Sections below are sent as the system prompt, the document, the instructions and
the task, in that order — instructions after the document, so a rule sits next
to the text it governs. Braces are placeholders filled by `src/lib.py` — do not
use a literal brace anywhere in this file.

## SYSTEM

You build a research dataset of contract clauses whose drafting caused a
dispute. You are given a court opinion, the contracts filed in that case, and a
numbered list of every clause those contracts contain. You identify which listed
clauses the parties disputed, and name each separate defect the court construed
in them.

### What you produce

- You **select and explain**. You do not transcribe, and you do not delimit.
- A clause is named by its `clause_id`, taken from the list. That is the only
  way to refer to one. You cannot report a line range, and you should not try:
  if the language the court construed sits inside clause `c042`, the answer is
  `c042`, whether the court quoted one phrase of it or all of it.
- For each defect you also cite the lines of the OPINION where the court
  construes it, and write one sentence naming the defect.

## DOCUMENT

### The opinion in {citation}

---------- OPINION START ----------
{opinion}
---------- OPINION END ----------

### The contracts filed in this case, and their clauses

Each contract appears below in full, followed by the list of clauses step 1
found in it. The list gives each clause an id, the lines it occupies, and a
short name.

{contracts}

## INSTRUCTIONS

### Answer in clause ids

- Every clause you report must be named by a `clause_id` from the list for the
  contract you name, and `contract_id` must be the contract that list belongs
  to.
- Find the language the court construed in the contract text, read off the line
  it sits on, and take the clause whose line range contains it. That is your
  answer.
- Where the court's discussion covers a run of clauses, report **each** of them
  that was genuinely disputed, as separate entries with their own ids. Do not
  pick one to stand for the group.
- Where the language spans two listed clauses — the court construes a sentence
  that step 1 split — report both.

### Which clause to report

**A clause is an answer when the opinion shows the two sides disputed it and the
court discussed it. How the court decided does not matter.**

The dataset is about drafting that causes litigation, not about who won. A
clause that had to be argued over in a federal court is risky whether or not the
court ended up agreeing with the party that drafted it — we would rather the
dispute had never arisen.

So, explicitly:

- A clause the court examined and **upheld** belongs in the list.
- A clause the court held **clear**, **plain** or **unambiguous** belongs in the
  list. That the parties needed a court to tell them so is the point.
- A clause the court construed **against** the drafter, or found ambiguous,
  belongs in the list.
- Whichever party won, and on whatever ground, does not change the answer.

What is required is evidence in the opinion itself, of either kind:

- the parties advancing **competing readings** of the clause; or
- the court **discussing or construing** the clause in its own analysis.

A clause merely quoted in passing, listed in a recital of the facts, or cited
for background with no dispute and no discussion, is not an answer.

Every issue you report must cite the passage of the opinion that shows it.
That citation is the evidence. A defect you cannot tie to the court's own words
does not belong in the list, and neither does a clause with no such defect.

### The dispute must match the risk type

- The risk types listed in the task come from the Westlaw key the case was filed
  under and from that key's headnote. They are facts about the case, not
  judgements for you to make.
- Report an issue only when the defect it names **is one of those types**. If
  the parties clearly fought over something else — a different kind of drafting
  defect, or a question that is not about the drafting at all — that is not an
  answer, however heated the dispute was. A clause whose every defect falls
  outside the list drops out with them.
- Expect this to exclude very little. Most cases are filed under a single risk
  type, and the disputes the opinion discusses are usually that type. Use it to
  drop a clause that plainly does not fit, not as a reason to be selective.

### One entry per distinct issue

A clause is reported **once**, under the id step 1 gave it. What can repeat is
the `issues` list inside it: one entry per **distinct defect the court
construed**, each carrying its own risk type and its own passage.

Two defects in the same listed clause are two issues on that one clause. Two
defects in two different listed clauses are two entries, one issue each. Which
of those you are looking at is settled by the list, not by you — so this is not
a judgement about boundaries, only about how many distinct defects the court
found.

**Two entries, when the court construed two different problems.** A clause can
fail in more than one way, and courts often take them in turn:

- two different phrases in the same clause, each argued to be ambiguous — two
  issues, both `1.1`;
- a term the court finds ambiguous on its face *and* a conflict between this
  clause and another section — two issues, `1.1` and `2.1`;
- the same phrase characterised two ways: ambiguous read in isolation, *and*
  resolved by reading the instrument as a whole — two issues, `1.1` and `2.2`,
  because those are two different defects even though the words are the same.

**One entry, when it is one problem discussed more than once.** A court that
returns to a defect later in the opinion has not construed a second defect:

- the same defect stated in the analysis and again in the conclusion;
- two passages that say the same thing in different words;
- a defect the court states, then illustrates with an example or applies to the
  facts;
- the same defect reached again while answering the other side's rebuttal.

Where that happens, report the issue **once**, citing the passage that shows the
defect most fully.

**The test.** Write the `issue` sentence for each entry you are considering. If
one sentence honestly describes both, it is **one** issue and you must not split
it. If describing both truthfully needs two different sentences — different
words at stake, or a different kind of defect — they are two.

Wording is not the test. Two passages phrased quite differently are still one
issue when they are about the same defect, and that is the common case: it is
how an opinion is written, not evidence of a second problem.

**Two issues may cite the same passage.** A court often disposes of two defects
in a single paragraph. Where it does, both entries point at the same lines, and
that is correct. Do not merge two real defects in order to keep their passages
distinct, and do not widen or shift a passage to make them look different — the
`issue` sentence is what separates the entries, not the line range.

Err toward one. One issue per clause is the normal case; two is uncommon; three
is rare and needs the opinion to show three plainly.

### Choosing the risk type for an issue

The task shows you two lists, and they do different jobs.

- **The full taxonomy** is the whole scheme these codes are drawn from. It is
  there so you can see what each code means against its neighbours — a code
  reads differently once you know what the others cover.
- **The candidates** are the codes *this case* was filed under. Every issue must
  carry one of them, and nothing outside that list is accepted.

Each issue carries **exactly one** code: the kind of defect that issue is. A
clause that fails in two ways under two codes is two issues, not one issue with
two codes.

Where the case lists **one** candidate, that code is the answer for every issue
you report — there is nothing to choose.

Where it lists **several**, decide from the passage you are citing which one
*that issue* turned on:

- Do not give an issue a code merely because the case carries it. The case's
  keys cover everything the court did across the whole opinion, and one issue is
  usually only part of that. A code you cannot point at in the passage you are
  citing is the wrong code for that issue.
- Do not invent a second issue in order to use a second candidate code. Where
  the court construed one defect, one entry is the complete answer even if the
  case was filed under four keys.
- Different clauses of the same case may well carry different codes. That is
  expected, not a contradiction.

#### How the court's own words show the type

Courts do not label these types, but they do not treat them alike either, and
the difference shows in the passage you are citing.

**Where the court is working on a relation between provisions, it says so, and
usually by name.** Each phrase below is several times more common in a
relational defect's passage than in an intrinsic one, and the first two never
appear in an intrinsic one at all:

- `harmonize`, `harmonized`, `in harmony`
- `irreconcilable`, `cannot be reconciled`
- `surplusage`, `nugatory`, `superfluous`, `redundant`
- would `render` something `meaningless`
- `read as a whole`, `in its entirety`
- `give effect to all`, `give effect to every provision`
- the specific `controls`, `governs` or `prevails` over the general

One of these is strong evidence the defect is relational: the court is reaching
for a canon that only means anything when two texts are in play. Name the other
text in your `issue` sentence.

**Where the trouble is inside one provision, the court reaches for no canon at
all.** There is no vocabulary to look for here, and you must not invent one.
What the court DOES is the signal: it quotes a word or phrase of the provision
and sets out the two readings the parties advanced of THAT language. The defect
is the word, and your sentence should name it.

**These are used for BOTH types and prove nothing on their own:** `ambiguous`,
`ambiguity`, `unambiguous`, `plain meaning`, `plain language`, `ordinary
meaning`, `reasonable interpretation`, `reasonably susceptible`, and `the term`
or `the phrase` followed by a quotation. `Ambiguous` above all is the court's
CONCLUSION, and a court reaches it as readily from two provisions that will not
sit together as from one word carrying two meanings. `conflict` and
`inconsistent` lean relational but appear in intrinsic passages too often to
settle anything. So do `read together` and `read with`: any two provisions can
be described as read together, including the two readings of a single ambiguous
phrase, so those words are not evidence of anything on their own. Never assign a
type because one of these appeared.

### When no listed clause is the answer

Sometimes the language the court construed is not in the lists at all. That
happens legitimately, and often:

- the opinion turns on an agreement that was never filed, or on a different one
  from the agreements shown;
- a filed block is only part of a longer contract, and the construed page is
  missing;
- the OCR lost the page the clause was on;
- step 1 enumerated the contract but missed that particular clause.

**Do not substitute.** Do not report the nearest similar clause, do not report
the section that surrounds the missing language, and do not report a clause from
the wrong agreement in order to return something. A wrong id is worse than no
answer, because nothing downstream can tell it was wrong.

Put it in `unlocated` instead: what the court was construing, and the opinion
lines that show it. That records the miss without inventing a clause. Returning
an empty `disputed` list with everything in `unlocated` is a valid answer.

## TASK

**Case:** {citation}

### The risk-type taxonomy, in full

Every code the scheme has. This is background: read it to see what each code
means against the others. It is **not** the list you may answer from.

{taxonomy}

### The candidate risk types for this case

These come from the Westlaw keys this case was filed under. **Every code you
return must be one of these.**

{risks}

### Headnotes

{headnotes}

### What to return

1. `case_desc` — one line: the parties, and what the construction dispute was
   about.

2. `disputed` — every **listed** clause the parties disputed and the court
   discussed, where at least one defect falls under one of the risk types
   listed. Each clause appears **once**. For each one:

   - `contract_id` — the contract whose clause list the id comes from.
   - `clause_id` — the id from that list, e.g. `c042`. Nothing else identifies
     a clause, and an id that is not in that contract's list is rejected.
   - `issues` — one entry per **distinct defect** the court construed in this
     clause. Usually one entry. For each:

     - `risk_type` — the single candidate code this defect falls under.
     - `issue` — one sentence: which words of the clause, and what was wrong
       with them. Write it in the court's own language for THIS dispute — the
       terms it quotes, the readings it sets out — together with the standard
       vocabulary for the type you assigned, so a defect of one type does not
       read like a defect of the other. Do not quote the opinion at length. It
       must be specific enough that a *different* defect in the same clause
       would need a different sentence, because that is exactly the test for
       whether you are looking at one issue or two.
     - `type_evidence` — the court's own words, copied exactly from the passage,
       that bear on WHICH TYPE this defect is. An empty string is a real and
       common answer: for a defect inside one provision the court usually
       reaches for no canon, and inventing one would be worse than saying so.
     - `type_evidence_conflict` — true when those words point at a different
       type than the code you assigned. This does happen. The candidates come
       from the case's Westlaw keys, and where the case lists one candidate you
       must use it even if the court's language for this particular defect
       points the other way. Record that rather than hiding it: do not change
       the code, and do not soften the `issue` sentence to make it agree.
     - `opinion_comment_start_line`, `opinion_comment_end_line` — the first and
       last line **in the opinion** of the passage showing this defect: the
       competing readings the parties advanced and the court's discussion of
       them. Where the court discusses the same defect in several places, cite
       the one passage that shows it most fully rather than adding an entry.

3. `unlocated` — anything the court construed that no listed clause contains.
   Leave it empty when there is nothing. For each:

   - `description` — what the court was construing, in your own words.
   - `why` — why it is not in the lists, as far as you can tell: the agreement
     was not filed, the page is missing, the clause list skipped it, or you
     cannot say.
   - `opinion_comment_start_line`, `opinion_comment_end_line` — the opinion
     lines that show the court construing it.

# Does the detection prompt's own type test reproduce the recorded types?

An audit, not an annotation step. Every issue below was recorded by step 2 with
a risk type, and that type is **not** shown here. This step applies the deciding
test exactly as the detection prompt states it and reports the type that test
yields.

What it is for: the agent finds the right clause and names the right defect, and
then files it under the other type from the one the data records. Either the
model is applying our test badly, or our test does not agree with the types the
data carries. Only one of those is worth spending more money on, and this tells
them apart.

Sections below are sent as the system prompt, the document, the instructions and
the task, in that order. Braces are placeholders filled by `src/lib.py` — do not
use a literal brace anywhere in this file.

## SYSTEM

You are auditing a classification rule against data it was meant to reproduce.

### The rule, quoted exactly as the detection prompt states it

> A useful test is to ask where the trouble lies. If it is **inside this
> provision** — a word that can be read more than one way, a writing mistake, a
> general term whose reach against its own list is unclear — that points to
> **type 1**. If it is in **how this provision stands with another part of the
> instrument** — the two cannot both be given effect, or what this one means only
> settles once they are read together, or a recital pulls against operative
> words — that points to **type 2**.
>
> Treat it as a guide rather than a rule: where a defect genuinely sits across
> both, give it the type the dispute would most likely turn on. Apply it to the
> defect you have named, not to how you came to notice it or how you explain it.
>
> - **What the other provision is doing decides the type.** Where it is evidence
>   of what a word in this provision means, the words here are what needs repair
>   and the issue is type 1. Where the dispute is about the two together, the
>   issue is type 2.
> - **A term used inconsistently across the contract is normally type 1.** The
>   defect is that the term was never pinned down; name the term.
> - **Type 2 is not only direct conflict.** Provisions that can be harmonised, but
>   only by deciding what they mean together, and a recital pulling against
>   operative words, are type 2 as much as two clauses that cannot both stand.

That is the whole of the rule. Nothing else decides the type.

### What you are doing

For each recorded issue you are given the provision it was found in and the
sentence naming the defect. Apply the rule above **to that defect** and report
the type the rule yields.

Three things this is not:

- **Not a review of whether the issue is real, or well described.** It is
  settled. Answer for the defect as written, even where you would have put it
  differently.
- **Not your own view of the right taxonomy.** Where the rule as written points
  one way and your instinct points the other, report the rule's answer and say
  so in `note`. That disagreement is the most useful thing you can record.
- **Not a guess at what an annotator wrote.** You are not shown the recorded
  type, and reasoning about which one it probably was would defeat the audit.

### How clearly the rule decides it

- `clear` — the rule points one way, and a careful reader applying it honestly
  would land there.
- `arguable` — the rule can be applied either way on this defect; give the more
  natural reading.
- `underdetermined` — the rule genuinely does not decide this one.

## DOCUMENT

### The contract these provisions come from

Shown in full, so that you can see the other provisions a defect might depend
on. Every recorded issue below was found in this document.

---------- CONTRACT {contract_id} START ----------
{document}
---------- CONTRACT {contract_id} END ----------

## INSTRUCTIONS

Take the issues one at a time, in the order given.

1. **Read the provision, and the sentence naming the defect.**
2. **Ask what would have to change to remove the dispute that defect creates.**
   Name it concretely — a term to be defined, a sentence to be rewritten, a
   choice between two provisions to be made express.
3. **If rewriting that provision alone would ordinarily do it, the rule yields
   `1`.** If the provision is sound as written and the fix lies in another
   provision, or in deciding which of two governs, the rule yields `2`.
4. **Record how clearly the rule decides it**, and one sentence in `rewrite`
   saying what would have to be changed.

Answer for every issue id listed, once each, and nothing else.

## TASK

### The recorded issues

Each carries the provision it was found in and the sentence naming the defect.

{issues}

### What to return

One entry per issue id above, in the same order.

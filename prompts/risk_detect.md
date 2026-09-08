# Risk detection — few-shot with judicial reasoning, one plain call

One call judges a batch of provisions from one contract. The model is given the
whole contract, the provisions to judge, and one worked example per risk type —
each example being a clause a court actually construed, together with the
passage of the opinion showing the dispute.

Sections below are sent as the system prompt, the document, the instructions and
the task, in that order. Braces are placeholders filled by `src/lib.py` — do not
use a literal brace anywhere in this file.

## SYSTEM

You are a precise contract-construction analyst.

You are given the full text of one contract and a list of provisions taken
verbatim from it, and you judge each provision for two independent kinds of
construction risk.

### The two risk types

Every issue is one of two types, and you report `1` or `2`.

**RISK TYPE 1 — the defect is in this provision's own words.** You may need the
rest of the contract to notice it, and your explanation will often cite other
provisions to show it, but the words needing repair are these words.

**RISK TYPE 2 — the defect is relational.** The provision's own words are not
what is in doubt. What is in doubt is how it stands with another part of the
instrument: whether the two can both be given effect, what this one means once
they are read together, or which of them controls.

**LOWER RISK** — nothing in the provision's wording, and nothing in its fit with
the rest of the instrument, gives a court something to construe. In any contract
only a small minority of provisions ever become the subject of a construction
dispute, so **most provisions should receive low probabilities.**

The sub-categories are illustrations of the range, not a checklist and not what
you report:

- **Type 1** — **1.1** lexical ambiguity or vagueness, where a word carries more
  than one reasonable meaning or has no applicable boundary (name the term);
  **1.2** a mechanical error in grammar, spelling or punctuation that changes the
  meaning; **1.3** general-vs-specific / list scope, a catch-all sitting against
  enumerated specifics (ejusdem generis, expressio unius), or drafting so
  one-sided that a genuine ambiguity goes against its drafter.
- **Type 2** — **2.1** direct conflict, where this clause cannot be squared with
  another of the same contract; **2.2** whole-contract coherence, where the
  clause's meaning only comes out, or falls apart, when the contract is read as
  a whole; **2.3** a recital conflicting with, or argued to control, the
  operative terms.

### Detection guidelines

#### What counts as a risk

A provision is risky when a court would have something to construe: the two
sides could read it differently and the dispute would turn on the words. This is
**not** a judgement about whether the provision is important, complex or
commercially significant. A long, carefully drafted limitation of liability is
usually lower risk; a short phrase like `Free land` can be high risk.

#### A way to look for the relational reading

Most provisions have a counterpart somewhere — the section they are made
subject to, the definition they turn on, the clause covering the same ground
more generally or more specifically, the amendment or endorsement that
qualifies them, the recital reciting what they were for. It is often worth
asking which one that is here, and then
whether reading them together changes what this one means. Read this provision
the natural way: does that leave the other with nothing left to do, contradict
what the instrument plainly does elsewhere, or turn the answer on what the two
say in combination?

This is a way of looking, not a step you owe an answer to. Usually the
counterpart turns out to be consistent and there is nothing to report. Where it
does not, name the other provision — that is what a type-2 issue is.

#### Assigning the type

A useful test is to ask where the trouble lies. If it is **inside this
provision** — a word that can be read more than one way, a writing mistake, a
general term whose reach against its own list is unclear — that points to
**type 1**. If it is in **how this provision stands with another part of the
instrument** — the two cannot both be given effect, or what this one means only
settles once they are read together, or a recital pulls against operative
words — that points to **type 2**.

Treat it as a guide rather than a rule: where a defect genuinely sits across
both, give it the type the dispute would most likely turn on. Apply it to the
defect you have named, not to how you came to notice it or how you explain it.

- **What the other provision is doing decides the type.** Where it is evidence
  of what a word in this provision means, the words here are what needs repair
  and the issue is type 1. Where the dispute is about the two together, the
  issue is type 2.
- **A term used inconsistently across the contract is normally type 1.** The
  defect is that the term was never pinned down; name the term.
- **Type 2 is not only direct conflict.** Provisions that can be harmonised, but
  only by deciding what they mean together, and a recital pulling against
  operative words, are type 2 as much as two clauses that cannot both stand.

#### What the worked examples do and do not tell you

Each example pairs a provision with the court's own words about it, and one
short passage of those words is set apart as the defect — that is the kind of
thing an `issue` entry names.

The high-risk examples are provisions a federal court **did** construe: direct
evidence that two parties read the same words differently and a judge had to
decide between them. The lower-risk examples are only provisions no court
construed **in that case**. That is much weaker evidence, and it is **not**
evidence that the provision is sound: it may be well drafted, or it may carry a
latent defect nobody had occasion to fight over, since a dispute has to be worth
the cost of litigating and most defects never are.

So do not treat a lower-risk example as a model of good drafting to match. Treat
the pair as two ends of a scale: what did a court find worth arguing about, and
what, in the same document, did nobody reach for? Your probabilities express
**how likely it is that a court would have something to construe here** — not a
verdict that the provision is sound.

### Calibration

Two instructions that pull against each other on purpose. Hold both.

**Be generous with entries.** If you can put a doubt into a sentence, write it
down, however unsure you are. A 0.01 entry is a real answer, and it does real work: 
it ranks the provision above one you left empty. Silence is for a provision you 
looked at and found nothing nameable in. It is never the way to express that a 
doubt seemed too small to write down.

**Be sparing above 0.5.** In the contracts the worked examples come from, a
court construed only a handful of provisions out of many hundreds — on the
order of one or two in a hundred. Your own rate of scoring 0.5 or above should
look about like that. Reaching 0.5 means naming the specific thing: for
**type 1** the ambiguous term or the general-vs-specific tension, for **type 2**
the other part of the instrument this one stands against, and what the two
together leave open.

Between those two ends use the whole range — 0.03, 0.17, 0.44, 0.71 and 0.92 all mean
different things. The probability is that a court would have something to
construe on account of the defect, not your confidence in how it would decide.

## DOCUMENT

### The worked examples

{examples}

### The contract to judge

- **contract_id:** {contract_id}
- **Filed in:** {citation}

---------- CONTRACT {contract_id} START ----------
{document}
---------- CONTRACT {contract_id} END ----------

## INSTRUCTIONS

Work in this order.

1. **Read the examples first.** For each one, read the provision and then the
   court's own words about it. What did the two sides actually argue, and what
   did the court find uncertain? That is the standard to apply — not your own
   sense of what looks like a badly drafted clause.
2. **Read the contract.** You need it for both types: a conflict cannot be seen
   from one provision alone, and how a term is used elsewhere often shows it was
   never pinned down here.
3. **Judge each listed provision**, in the order given.

### What to return for each provision: a list of issues

A provision may carry more than one thing a court could be asked to construe, of
different strengths, so you return an **issue list** rather than one number.
Each issue is three fields:

- `issue` — two sentences naming **this specific defect**. For risk type 1,
  name the ambiguous term or the general-vs-specific tension. For risk type 2,
  **name the other part of the instrument** this one stands against, and say
  what the two together leave open. One issue, one defect: if you find yourself
  writing "and also", that is a second issue.
- `type` — `1` if the trouble is inside this provision's own words; `2` if it is
  in how this provision stands with another part of the instrument.
- `prob` in (0, 1] — that a court would have something to construe **on account
  of this issue**. To **two decimal places**, on a 0.01 grid: 0.03, 0.17, 0.62,
  not rounded to the nearest 0.05 or 0.1, since the fine distinctions are what
  the number is for. Greater than zero, always.

**Ask about both types before moving on.** Having named a defect in the
provision's own words, ask separately whether it also sits badly against another
provision; having named a conflict, ask whether the words themselves are loose.
Where you can name both, list both. The second entry is held to the same
standard as the first — that you can say what is wrong, not that you are
confident — so it belongs in the list at 0.10 as readily as at 0.60.

A type with no entry is read as probability 0, and an empty list puts the
provision below every provision you named something in. Both are correct when
there was nothing to name, and wrong when there was something you passed over.

Probabilities are per issue and independent: they are not shares of anything and
do not sum to 1.

## TASK

### The provisions to judge

Each is quoted verbatim from the contract above, under its id in square
brackets.

{clauses}

Return one judgment for every provision id listed here, in this order, and
nothing else.

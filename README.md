# Contract-clause risk dataset

A labelled dataset of **risky vs non-risky contract clauses**, built from federal
court opinions and the contracts filed with them.

**Design doc: [docs/DESIGN.md](docs/DESIGN.md)** — how and why the pipeline works
the way it does. This README is the repo tour and how to run it.

Known limitations, and what the pipeline does *not* catch, are in
[DESIGN.md §8](docs/DESIGN.md).

## What "risky" means

> **A clause is positive when the court's opinion shows the two sides disputed it
> and the court discussed it — whatever the court decided.**

We do not want a clause in a contract to lead to a court dispute *at all*.
Whether we would go on to win that dispute is beside the point: the litigation is
itself the cost. A clause that had to be argued over in front of a federal judge
has already failed at its job, even where the judge ultimately read it the way
its drafter intended.

So a clause the court **upheld** is positive. A clause the court held **clear**
or **unambiguous** is positive — that the parties needed a court to tell them so
is exactly the failure. A clause found ambiguous or construed against its drafter
is positive. Every positive cites the passage of the opinion showing the dispute,
and that passage is stored on the row.

> **A clause is negative when it is any other clause of any contract filed in
> the same case** — every clause whose line window does not intersect a
> positive's.

A positive's own contract always contributes negatives, so the two classes are
matched on the document and on the OCR condition and a classifier cannot win by
recognising document style or scan quality. The other agreements of the case
contribute negatives too: they were filed, they were before the court, and the
court did not construe them. Sixteen of the 67 contracts contain no positive at
all, which makes them the only documents here where the right answer is "nothing
to flag".

**The classes are not matched on clause length, and deliberately so.** Positives
run longer — median 607 characters against 372, with P(a positive is longer than
a negative) = **0.655**, where 0.50 would mean length carries no information.
That is not a construction artefact: the clauses parties take to court are the
long, qualified, heavily conditioned ones, while a contract's other provisions
include short definitions, notice clauses and boilerplate nobody disputed.

Length is left in rather than sampled away — the dataset's job is to record what
the corpus is, not to look balanced. `contract_id` and `clause_text` are columns,
so cap, weight or stratify on your own terms. **If you benchmark on this, report
a length-only baseline alongside your model**, and report per-contract as well as
aggregate numbers.

The risk *type* is never a model's opinion either: it comes from the Westlaw Key
Number the case was filed under, and a code outside the case's own set is
rejected.

## How it works, in one paragraph

The model never writes clause text. For each clause it returns a line range plus
a short verbatim anchor at each end — the first and last eight words, copied
exactly as the scan shows them, OCR damage and all. `lib.locate()` matches those
anchors against the contract file, snaps the boundary to where they actually are,
and slices the text out of the file itself. Nothing the model writes reaches the
dataset; the anchors only say *where*.

## Layout

```
data/                    Westlaw headnotes, opinion text, the docket linking sheet
contract_risk/           the Contract-Risk repo's new_approach/ — OCR'd contract text

prompts/                 <name>.md (SYSTEM/DOCUMENT/INSTRUCTIONS/TASK) + <name>.schema.json
  layout                 step 0b — is the scan an interleaved two-column page?
  extract                step 1 — locate the clauses the parties disputed
  inventory              step 2 — locate every clause of one contract

src/lib.py               paths, taxonomy, ask(), numbered(), CLAUSE, locate(), normalise()
src/step0_corpus.py      0. link cases, register documents         (no LLM)
src/step0b_layout.py     0b. reject two-column scans               (Sonnet, per contract)
src/step1_extract.py     1. which clauses were disputed            (LLM, per case)
src/step2_inventory.py   2. every clause of every contract         (LLM, per contract)
src/build_dataset.py        assemble + validate -> dataset.csv     (no LLM)
src/replay_anchors.py       score the locator against stored logs  (no LLM, no cost)

output/cases.json        cases in scope, with keys, headnotes and codes
output/contracts.json    the document registry
output/contracts/<cid>.md   document text, OCR furniture stripped
output/layout.json       step 0b — the two-column verdict, with the model's evidence
output/opinions/<id>.txt    opinion text, keyed by case id
output/clauses.json      step 1 — positives, and what was rejected
output/inventory.json    step 2 — every clause of every contract, and the §8 flags
output/llm_logs/<step>/  full prompt, response and usage for every call
output/dataset.csv       the dataset
```

`data/` and `contract_risk/` are gitignored — they are large and are provided
separately. Everything the code reads lives inside the repo; no path escapes it,
so on a machine where those two sit elsewhere, link them in rather than reaching
out of the tree:

```sh
ln -s /path/to/legal-llm-data                  data           # or a Windows junction
ln -s /path/to/Contract-Risk/new_approach      contract_risk
```

## Where the contract text comes from

Not from OCR run here. The [Contract-Risk](https://github.com/) repo already did
the acquisition, and step 0 reads its output:

1. downloaded the docket filings for a sample of cases and OCR'd them
   (`ocrmypdf --force-ocr`);
2. had a model say which downloaded file holds which named agreement;
3. sliced that file down to the contract's own lines — a **verbatim** line-range
   cut. Their `extract_contracts.py` has the model return line numbers and does
   the cutting itself, for the same reason this pipeline does, so no model wrote
   the words. Independently checked here: all 205 re-sliceable extractions are
   byte-identical to their source at the recorded spans.
4. checked each result against the opinion — is this the contract the court
   construed, and is it readable? That is `contract_check.csv`, and step 0
   registers only the `usable` ones unless `--verdict` says otherwise.

Every row of `contracts.json` carries their agreement name, source file, line
spans, extraction confidence and verdict, so a clause can be traced back to the
OCR it was cut from without opening their repo.

This moves one judgement upstream. **Which document is the contract** used to be
step 1's problem — it was handed a whole bundle of filings and had to ignore the
briefs and cover sheets. Now that answer arrives with the data. Step 1 is handed
contracts, which is cheaper and better targeted, but the choice is theirs and a
wrong one is no longer caught here.

## Running

```sh
pip install -r requirements.txt
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

python src/step0_corpus.py       # 0.  no LLM
python src/step0b_layout.py      # 0b. one cheap Sonnet call per contract (~$5 for 117)
python src/step1_extract.py      # 1.  one call per case
python src/step2_inventory.py    # 2.  one call per contract step 1 was shown
python src/build_dataset.py      #     no LLM — validates, then writes
```

`--case "44 F.Supp.3d 736"` restricts any step to one citation, which is how to
try the pipeline end to end before paying for a full run. `step0b_layout.py` and
`step2_inventory.py` also take `--contract <contract_id>`.

**Steps 0b, 1 and 2 are resumable.** Each writes its artifact after every call
and re-runs only what is missing, so an interrupted run picks up where it
stopped. To *redo* work already done — after a prompt change, say — delete that
case or contract id from `clauses.json` / `inventory.json` first, or the step
will skip it and you will conclude the change had no effect. Step 0b takes
`--force` instead.

Step 0b rejects nothing but two-column scans, and rejects them by writing a
boolean to `layout.json` — the contract file and its registry row are untouched,
and a verdict you disagree with is undone by editing that boolean.

Step 0 and `build_dataset.py` make no model calls, so the dataset is reproducible
from the stored artifacts without an API key.

### Tuning the locator for free

A log holds both the numbered document that was sent and the model's answer, so
the locator can be re-scored offline at no API cost:

```sh
python src/replay_anchors.py --logs output/llm_logs
```

That **replays** the anchors the model actually returned, which is how
`ANCHOR_MATCH` and `SLACK` are tuned against real answers. `ANCHOR_WORDS` cannot
be swept this way — you cannot lengthen an anchor that was already written. For
that the script needs logs whose clauses carry full clause text, from which it
**synthesises** anchors of any width; point `--logs` at such a directory and
`--anchor-words 6` then means something.

It reports what fraction of anchors match, how often snapping moves a boundary
and by how much, and how often the extracted text differs from the raw source
window. A difference is not automatically a fault — the anchors cut *inside* the
first and last line, so a heading's markdown is legitimately left out of the
span. What it catches is a span that lost real words.

## What guarantees the text

Nothing the model writes reaches the dataset. `locate()` is the only route, and
it only ever returns a slice of the contract file.

An anchor that matches nothing near the claimed window means the model pointed at
the wrong place, and the clause is rejected with its reason recorded.
`build_dataset.py` then re-cuts **every** row from disk at its recorded character
span and refuses to write unless it reproduces `clause_text` exactly.

What this does *not* catch — over-capture between two correct anchors, unchecked
`clause_name`, non-reproducible clause selection — is set out in
[DESIGN.md §8](docs/DESIGN.md), along with the flags step 2 raises for the first
of them. Two-column OCR interleave used to be on that list; it is now screened
out by step 0b, whose measurements are in the same section.

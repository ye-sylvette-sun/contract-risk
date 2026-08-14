# Contract-clause risk dataset

A labelled dataset of **risky vs non-risky contract clauses**, built from federal
court opinions and the contracts filed with them.

**Design doc: [docs/DESIGN.md](docs/DESIGN.md)** — how and why the pipeline works
the way it does. This README is the repo tour and how to run it.

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

> **A clause is negative when it is any other clause of the same contract** —
> every clause whose line window does not intersect a positive's.

Negatives come from the same document on purpose. The two classes are then
matched on the document, the OCR condition and the clause granularity, so a
classifier cannot win by recognising document style, scan quality or clause
length. It has to read the clause.

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
bloomberg/               contract bundles as downloaded (PDF)
bloomberg_datalab/       the same bundles after Datalab Marker OCR (markdown)

prompts/                 <name>.md (SYSTEM/DOCUMENT/INSTRUCTIONS/TASK) + <name>.schema.json
  extract                step 1 — locate the clauses the parties disputed
  inventory              step 2 — locate every clause of a winning contract

src/lib.py               paths, taxonomy, ask(), numbered(), CLAUSE, locate(), normalise()
src/step0_corpus.py      0. link cases, register documents         (no LLM)
src/step1_extract.py     1. which clauses were disputed            (LLM, per case)
src/step2_inventory.py   2. every clause of the contracts that won (LLM, per contract)
src/build_dataset.py        assemble + validate -> dataset.csv     (no LLM)
src/replay_anchors.py       score the locator against stored logs  (no LLM, no cost)

output/cases.json        cases in scope, with keys, headnotes and codes
output/contracts.json    the document registry
output/contracts/<cid>.md   document text, OCR furniture stripped
output/opinions/<id>.txt    opinion text, keyed by case id
output/clauses.json      step 1 — positives, and what was rejected
output/inventory.json    step 2 — every clause of the winners, and the §8 flags
output/llm_logs/<step>/  full prompt, response and usage for every call
output/dataset.csv       the dataset
```

`data/`, `bloomberg/` and `bloomberg_datalab/` are gitignored — they are large
and are provided separately. Everything the code reads lives inside the repo; no
path escapes it.

## Running

```sh
pip install -r requirements.txt
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

python src/step0_corpus.py       # 0.  no LLM
#    ... download the shortlisted bundles, OCR them with Datalab, re-run step 0 ...
python src/step1_extract.py      # 1.  one call per case
python src/step2_inventory.py    # 2.  one call per contract that produced a positive
python src/build_dataset.py      #     no LLM — validates, then writes
```

`--case "44 F.Supp.3d 736"` restricts any step to one citation, which is how to
try the pipeline end to end before paying for a full run. `step2_inventory.py`
also takes `--contract <contract_id>`.

**Steps 1 and 2 are resumable.** Each writes its artifact after every call and
re-runs only what is missing, so an interrupted run picks up where it stopped. To
*redo* work already done — after a prompt change, say — delete that case or
contract id from `clauses.json` / `inventory.json` first, or the step will skip
it and you will conclude the change had no effect.

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

What this does *not* catch — over-capture between two correct anchors, two-column
OCR interleave, unchecked `clause_name`, non-reproducible clause selection — is
set out in [DESIGN.md §8](docs/DESIGN.md), along with the flags step 2 raises for
the first of them.

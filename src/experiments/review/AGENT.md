# Review difficulty — handoff

Read this before you touch anything in this directory. It says what these two
experiments are for, how they are designed, what they found, and how to run
them on a fresh machine.

| | rates | one call per | script |
|---|---|---|---|
| **case difficulty** | how hard a case, and what its court construed, is to understand | case | `review_case_difficulty.py` |
| **issue difficulty** | how hard one alignment call is to check, once the case is understood | case, one rating per call | `review_issue_difficulty.py` |

**In one line each:** case difficulty tracks mainly the **length and the
number** of things in a case, and is unrelated to the judge's scores. Issue
difficulty **is** related to the judge's score: calls at the extremes are easy
to check, calls in between are hard.

## Why they exist

The risk-detection agent (`../risk_detect/`) names issues on each provision, and
the alignment judge (`../issue_alignment/`, `gpt-5.6-sol`) says whether each
issue is a defect the court actually construed. A human has to check those
judge calls. From a Slack thread (October 2026):

- **Yiqin Fu** (law-trained fellow student) reviewed two cases. Aspect Software
  v. Barnett (`787 F.Supp.2d 118`, a noncompete) took **~10 minutes**: one
  phrase ("reasonably likely"), familiar doctrine, checkable at a glance.
  Presbyterian Healthcare v. Goldman Sachs (`122 F.Supp.3d 1157`) took
  **~3 hours** and she was still unsure: the defect spans the merger clause and
  the forum-selection clause plus a separate "Proposal"; she had to learn
  auction-rate bonds, swaps, FINRA arbitration vs. federal court and a
  precedent; she spent ~30 minutes just on who wants what procedurally. On the
  calls themselves: whether the agent's "pre-contract Proposal" was the court's
  "other agreements" took her about an hour; on clause 5.9 the agent's issue
  bundled a wrong claim with a missing one — the missing one was dispositive —
  and she could not say whether the judge's match was right. She said she
  cannot review all 50+ cases this quarter.
- **Julian Nyarko** (professor) asked Ye Sun to *systematically identify the
  easy and hard cases*, and guessed that easy cases have a very high or very
  low judge relevance (alignment) score and hard ones an intermediate score.

The case experiment rates every case before anyone reviews it, so the queue can
be triaged and Julian's guess tested. The guess failed at the case level, and
the reason pointed one level down — the judge's score belongs to one call, not
to a case — so the issue experiment rates every call, to test the guess where
the score lives.

**Both run on `gpt-6.1-sol`, effort `medium`** — on purpose not the alignment
judge's `gpt-5.6-sol`. They answer different questions about the same material,
and the analysis sets one against the other.

## Case difficulty

### Design

One call per case. The model sees **the case and the court's defects, and
nothing else**:

- the whole numbered opinion and every contract filed in the case;
- every provision the court construed, every defect the court construed in it
  (the dataset's `issues`: sentence, risk type, scope, opinion passage), with
  ids `d01, d02, …` across the case. A passage shared by two defects is printed
  once and referred to after.

It does **not** see the agent's issues or the judge's calls or scores, so the
difficulty can be compared against them without having been built from them.
The prompt says difficulty is **how hard the case and its defects are to
understand**, and that neither the number of court defects nor the length of
the documents is difficulty in itself; the time estimate counts the reading
that understanding requires and adds nothing per defect. The difficulty factors
are Yiqin's, written in general terms and without naming her two cases, so
those two cases can validate the prompt.

Output per case (`prompts/review_case_difficulty/review_case_difficulty.schema.json`):
`difficulty` (easy / intermediate / hard), `score` (1–10; 1–3 easy, 4–6
intermediate, 7–10 hard), `estimated_minutes`, five `factors` each with a
`level` (low / medium / high) and a note — `transaction_background`,
`domain_knowledge`, `defect_spread`, `court_reasoning`, `issue_distinctness` —
then `hardest_defect` and `reason`. The script adds `court_defects` (the count,
for analysis only) and `warnings`.

**Scope.** The risk-detection evaluation set (every case except the worked
examples' cases, `runs.held_out`): 50 cases. 8 have no court defect in the
dataset and are skipped — the court construed a document that is not in the
dataset (an email, the NACHA Rules, an unfiled writing, a separate agreement or
policy), construed nothing (a discovery ruling), or its construed contract has
no rows of its own (`109 Fed.Cl. 1`). **42 cases are rated.**

### Results

42 cases, no warnings, ~2.04M input and 26k output tokens (~$5.4 at the rates
below). Easy 13, intermediate 23, hard 6; scores 2–7 only.

| citation | case | difficulty | score | min | court defects | judged calls | mean call score | hard calls |
|---|---|---|---:|---:|---:|---:|---:|---:|
| 122 F.Supp.3d 1157 | Presbyterian Healthcare Services v. Goldman, Sachs | hard | 7 | 70 | 4 | 3 | 5.3 | 0 |
| 35 F.Supp.3d 725 | Sher v. Barclays Capital | hard | 7 | 60 | 25 | 34 | 3.3 | 1 |
| 694 F.Supp.2d 794 | Ohic Insurance v. Employers Reinsurance | hard | 7 | 70 | 8 | 6 | 2.2 | 0 |
| 785 F.Supp.2d 188 | Wells Fargo Bank v. ESM Fund I | hard | 7 | 65 | 7 | 6 | 3.3 | 1 |
| 894 F.3d 509 | Wayne Land & Mineral Grp. v. Del. River Basin | hard | 7 | 60 | 1 | 2 | 2.5 | 0 |
| 982 F.Supp.2d 518 | Trustees of the Univ. of Pennsylvania v. St. Jude | hard | 7 | 65 | 5 | 8 | 3.0 | 1 |
| 215 F.Supp.3d 756 | Dorchester Minerals v. Chesapeake Exploration | intermediate | 6 | 45 | 3 | 3 | 2.7 | 0 |
| 328 F.Supp.3d 317 | Wells Fargo Advisors v. Sappington | intermediate | 6 | 40 | 2 | 4 | 3.2 | 0 |
| 359 F.Supp.3d 268 | Chambers v. Chesapeake Appalachia | intermediate | 6 | 45 | 20 | 10 | 7.5 | 10 |
| 767 F.Supp.2d 1191 | COPIC Insurance v. Wells Fargo Bank | intermediate | 6 | 55 | 19 | 19 | 3.3 | 2 |
| 177 F.Supp.3d 561 | Doe v. Brandeis University | intermediate | 5 | 30 | 2 | 2 | 3.5 | 0 |
| 18 F.Supp.3d 456 | Fleisher v. Phoenix Life Insurance | intermediate | 5 | 45 | 9 | 5 | 4.8 | 1 |
| 303 F.Supp.3d 236 | N.Y. Times Co. v. Newspaper & Mail Deliverers' | intermediate | 5 | 35 | 1 | 2 | 7.0 | 1 |
| 527 B.R. 351 | Uecker v. Wells Fargo Capital Finance | intermediate | 5 | 40 | 8 | 8 | 3.1 | 0 |
| 57 F.Supp.3d 920 | Westfield Insurance v. Travelers Indemnity | intermediate | 5 | 30 | 2 | 2 | 1.5 | 0 |
| 808 F.Supp.2d 552 | Hard Rock Cafe International v. Hard Rock Hotel | intermediate | 5 | 45 | 12 | 11 | 4.0 | 3 |
| 874 F.Supp.2d 328 | Syncora Guarantee v. EMC Mortgage | intermediate | 5 | 40 | 4 | 4 | 3.5 | 0 |
| 2 F.Supp.3d 296 | Islip U-Slip v. Gander Mountain | intermediate | 4 | 25 | 4 | 3 | 2.0 | 0 |
| 20 F.Supp.3d 709 | Brenner ex rel. Plotkin v. National Outdoor Leadership School | intermediate | 4 | 25 | 4 | 2 | 4.0 | 0 |
| 252 F.Supp.3d 52 | Axia NetMedia v. Massachusetts Technology Park | intermediate | 4 | 25 | 3 | 4 | 4.0 | 1 |
| 268 F.Supp.3d 61 | Xenophon Strategies v. Jernigan Copeland | intermediate | 4 | 30 | 8 | 9 | 3.2 | 1 |
| 364 F.Supp.3d 990 | Arp Wave v. Salpeter | intermediate | 4 | 30 | 7 | 6 | 3.0 | 1 |
| 467 F.Supp.2d 115 | Diálogo v. Bauzá | intermediate | 4 | 25 | 2 | 2 | 2.5 | 0 |
| 694 F.Supp.2d 106 | Orbusneich Medical v. Boston Scientific | intermediate | 4 | 25 | 2 | 0 | — | 0 |
| 779 F.Supp.2d 416 | Kamco Industrial Sales v. Lovejoy | intermediate | 4 | 25 | 3 | 4 | 3.0 | 1 |
| 809 F.Supp.2d 582 | Lincoln General Insurance v. U.S. Auto Insurance | intermediate | 4 | 25 | 4 | 7 | 2.7 | 0 |
| 814 F.Supp.2d 930 | Pinel v. Aurora Loan Services | intermediate | 4 | 20 | 4 | 5 | 2.2 | 0 |
| 88 F.Supp.3d 1156 | Tri-Union Seafoods v. Starr Surplus Lines | intermediate | 4 | 30 | 6 | 5 | 3.4 | 0 |
| 99 F.Supp.3d 585 | Sköld v. Galderma Laboratories | intermediate | 4 | 30 | 3 | 3 | 2.7 | 0 |
| 129 F.Supp.3d 782 | Rembrandt Enterprises v. Illinois Union Insurance | easy | 3 | 15 | 3 | 4 | 1.5 | 0 |
| 161 F.Supp.3d 970 | Great Plains Ventures v. Liberty Mutual Fire | easy | 3 | 20 | 6 | 3 | 1.7 | 0 |
| 266 F.Supp.3d 666 | Lankau v. Luxoft Holding | easy | 3 | 15 | 2 | 2 | 5.0 | 0 |
| 266 F.Supp.3d 961 | EQT Production v. Magnum Hunter Production | easy | 3 | 12 | 1 | 2 | 1.5 | 0 |
| 565 B.R. 241 | ICICI Bank v. Essar Global Fund | easy | 3 | 18 | 2 | 1 | 7.0 | 1 |
| 68 F.Supp.3d 317 | Two Locks v. Kellogg Sales | easy | 3 | 25 | 3 | 3 | 3.7 | 0 |
| 702 F.Supp.2d 921 | Scott v. Regions Bank | easy | 3 | 12 | 1 | 1 | 1.0 | 0 |
| 817 F.Supp.2d 1357 | Rosen v. Protective Life Insurance | easy | 3 | 15 | 2 | 2 | 1.5 | 0 |
| 88 F.Supp.3d 27 | Proulx v. Brookdale Living Communities | easy | 3 | 20 | 4 | 5 | 3.8 | 1 |
| 940 F.Supp.2d 123 | Malmsteen v. Universal Music Group | easy | 3 | 20 | 3 | 3 | 4.0 | 0 |
| 97 F.Supp.3d 548 | Deutsche Bank National Trust v. Morgan Stanley | easy | 3 | 15 | 1 | 1 | 6.0 | 0 |
| 976 F.Supp.2d 606 | Haywood v. University of Pittsburgh | easy | 3 | 18 | 2 | 2 | 2.5 | 0 |
| 787 F.Supp.2d 118 | Aspect Software v. Barnett | easy | 2 | 10 | 1 | 1 | 6.0 | 0 |

The last three columns come from the issue experiment: the number of judged
calls in the case, their mean difficulty score, and how many are hard.

**Case difficulty tracks length and number.** Against 30 static features of the
case (Spearman, permutation p, Benjamini–Hochberg over the 30):

| feature | ρ with `score` | ρ with minutes | |
|---|---:|---:|---|
| contracts carrying a construed provision | +0.48 | +0.45 | q < 0.05 |
| court defects | +0.46 | +0.59 | q < 0.05 |
| construed provisions | +0.45 | +0.52 | q < 0.05 |
| input tokens (opinion + contracts + defects) | +0.32 | +0.38 | p < 0.05 |
| longest construed clause | +0.29 | +0.33 | p ≈ 0.06 |

Everything that describes the *nature* of the defects — share of risk type 2,
scope, Westlaw keys, headnotes — is near 0. The one count that keeps its
relation after controlling for the number of defects is the number of
contracts carrying one (ρ 0.38, p 0.01): a case whose defects sit in several
documents. Part of the count relation is expected of any rater that judges a
case by its hardest defect — more defects, more draws at a hard one — and these
data cannot separate that from the model being swayed by volume.

**The validation cases sit apart**: Goldman hard (7, ~70 min), Aspect easy
(2, ~10 min). The model underestimates the hard end — Goldman took three hours.

**Julian's guess does not hold at the case level**: per case, share of
lukewarm judge scores (0.5–0.9) vs. `score` ρ 0.06 (p 0.71), mean distance from
0.5 ρ 0.01 (p 0.94), the same after controlling for the number of judged
issues. Averaged over a case, the calls' own difficulty is unrelated to the
case's (ρ 0.06): how hard a call is to check is a property of the call.

## Issue difficulty

### Design

One call per case, **one rating per alignment call** — an *item*: one issue the
agent named on a construed provision, and what the judge said of it. The model
sees the case material above, the court defects with their `d` ids, and then
every item of the case, ids `a01, a02, …`:

- the provision, and the ids of the court defects on it — the candidates the
  judge was shown;
- the agent's description, verbatim;
- the judge's call: **matched to `dNN`**, or **matched to none** of them.

The difficulty asked for depends on the call. A match: how hard is it to decide
whether the description names that court defect. No match: how hard is it to
decide that it names none of them. In both, **only the work the item adds for a
reviewer who already understands the case**, so items do not inherit their
case's difficulty.

It does **not** see the judge's alignment score, the judge's reasoning or
quoted evidence, or the agent's probability. The score is what the result is
compared against; the reasoning is the answer the reviewer has to reach. The
call itself is shown because it is the thing being checked, and the prompt
tells the model not to decide whether it is right.

The prompt walks the four steps a reviewer takes — what the description is
about, whether what it asserts is true, whether that is what the court
construed, whether the call follows — and its factors are drawn from Yiqin's
account of checking calls, in general terms: a document named differently by
the two sides, factual claims to verify, several claims bundled with some
wrong, the right words but not the court's decisive point, multi-step
reasoning, a relational defect reached only in part. It says explicitly that
**how close** the description is to the court defect is not difficulty — a
clear partial match is as easy as a full one — and that a distinction between
two framings counts only where the court's construction turned on it.

Output per item (`prompts/review_issue_difficulty/review_issue_difficulty.schema.json`):
`difficulty`, `score` (same bands), `estimated_minutes`, `reason`. The script
adds, from its own records, `job_id`, `call` (`match` / `none`), `matched`,
`candidates` and the judge's `alignment` — copied after the call, for the
analysis — plus `warnings` per item and per case.

**Scope.** Every judged issue in `issue_alignment_check.json`: **209 items in
41 cases** (one rated case has no judged issue).

### Results

41 cases, 209 items, no warnings, ~2.06M input and 28k output tokens (~$5.4).
Easy 139, intermediate 44, hard 26; scores use 1–9.

**Issue difficulty is related to the judge's score.** In this judge run a score
of 0 and "no match" are the same 140 calls, none lie in (0, 0.5) and none is
exactly 1, so the score falls into three groups:

| judge score | items | mean score | mean minutes | hard | easy |
|---|---:|---:|---:|---:|---:|
| 0 (no match) | 140 | 2.8 | 3.6 | 4% | 81% |
| 0.5–0.9 | 38 | **6.1** | **12.4** | **53%** | 16% |
| ≥ 0.9 | 31 | 3.1 | 4.4 | 3% | 65% |

Permuting the judge's score **within each case**, so case effects stay fixed:

- middle minus the two ends: **+3.3** points (p < 0.001);
- among matched calls only, 0.5–0.9 minus ≥ 0.9: **+3.1** (p < 0.001) — this
  comparison is free of the confound that a 0 is also a different task
  (searching every candidate rather than checking one pair);
- on the 155 items with a single candidate, where the two tasks come to the
  same thing, the pattern holds: 2.8 / 5.7 / 2.9.

So **calls at the extremes are easy to check and calls in between are hard**.
Inside the middle the score is not uninformative: among matched calls,
difficulty falls as the score rises (ρ −0.75 over all 69; −0.48 within
0.5–0.9, p 0.002). The closer a match is to full, the easier it is to confirm.

Items do not inherit their case's difficulty (item vs. case score ρ 0.12,
p 0.09). Items with more candidate defects are somewhat harder (ρ 0.22).

### What the result does not show

- **Two models rating one pair.** The judge scores a call in the middle because
  it sees a partial match; the difficulty model, shown the same pair, finds it
  hard to decide. Their agreement shows that both models find the same calls
  unclear — not, yet, that a human does.
- **The one human data point disagrees.** Aspect's only call (judge 0.66) is
  rated intermediate 6; Yiqin found it easy. The model's reason is real — the
  court itself set "reasonably likely" as a question of meaning, not of scope
  (opinion line 44), and the agent's description leans toward scope — but it is
  a distinction she did not stop over.
- **Run-to-run noise is about ±2 points per item.** The same prompt rated the
  Goldman calls 5/5/3 in a trial and 5/6/5 in the full run, Aspect 7 then 6. The
  group means are steadier than any single item.

What would settle both experiments is **measured human review time and
confidence**, call by call, on a few cases across the bands — and especially on
calls the judge scored 0.5–0.9 against calls at the two ends.

## Files

```
prompts/review_case_difficulty/review_case_difficulty.{md,schema.json}     the case prompt + schema
prompts/review_issue_difficulty/review_issue_difficulty.{md,schema.json}   the issue prompt + schema
src/experiments/review/review_case_difficulty.py    the case run (and the helpers both share)
src/experiments/review/review_issue_difficulty.py   the issue run
output/review/review_case_difficulty.json           results, keyed by citation
output/review/review_issue_difficulty.json          results, keyed by citation, then item id
output/review/llm_logs/<prompt>/<case_id>.json      prompt sections, document digest, response, usage
```

Inputs are all committed: `output/dataset/` (dataset.csv, cases.json,
contracts.json, contracts/, opinions/), and for the issue run
`output/issue_alignment/issue_alignment_check.json`. `data/` and
`contract_risk/` are **not** needed.

## Running it on a new machine

1. **Code.** `git pull` on `main` of https://github.com/ye-sylvette-sun/contract-risk.
2. **Python.** Any env with the pins in `requirements.txt` except matplotlib,
   which these runs do not use (`matplotlib==3.8.4` has no Python 3.13 wheel, so
   `pip install -r requirements.txt` fails on 3.13 — install the rest, or use
   Python 3.12). What they need: `openai==2.54.0`, `tiktoken==0.14.0`,
   `httpx==0.28.1`, `python-dotenv==1.2.3`, plus what `runs.py` imports.
3. **Key.** Put `OPENAI_API_KEY=...` in `.env` at the repo root (gitignored;
   `lib.py` loads it).
4. **Dry run — no calls, no cost.**
   ```
   python src/experiments/review/review_case_difficulty.py --dry-run
   python src/experiments/review/review_issue_difficulty.py --dry-run
   ```
   Expect 42 cases, ~1.94M input tokens; and 41 cases, 209 items, ~1.95M.
5. **Validate on the two known cases first**, outside `output/`:
   ```
   python src/experiments/review/review_case_difficulty.py \
       --case "122 F.Supp.3d 1157" --case "787 F.Supp.2d 118" --out /tmp/rd_trial
   ```
   Goldman should come back **hard**, Aspect **easy**. The same flags work for
   the issue run. About $0.25 each.
6. **Full run.**
   ```
   python src/experiments/review/review_case_difficulty.py --parallel 4
   python src/experiments/review/review_issue_difficulty.py --parallel 4
   ```
   About **$5.5** each (~2.0M input tokens at $2.50/M, plus output at $10/M;
   the largest case is ~142k tokens, below the 272k long-context tier). Check
   current rates before trusting this.

### Resuming and failures

- The runs are **resumable**: a case already in the result file is skipped.
  Re-run the same command to continue.
- A failed case (dropped stream, exhausted account) is reported and the others
  keep going; the run exits non-zero listing the failures.
- A case whose call succeeded and was logged but never reached the result file
  is **recovered from its log at no cost** ("from log"), but only if the log is
  the identical request (model, prompt sections, document digest).
- **After changing a prompt**, delete its result file and its logs, or the old
  answers are kept for every case already done.

### Reproducing the analysis

Not scripted in the repo; it is short. Case level: Spearman of `score` and
`estimated_minutes` against per-case counts and lengths computed from
`dataset.csv` and the dry run's token counts, with a permutation p and
Benjamini–Hochberg over the features. Issue level: group items by `alignment`
(0 / [0.5, 0.9) / ≥ 0.9) and compare `score`; for significance, shuffle
`alignment` **within each case** (items of one case are not independent — up
to 34 in a case) and recompute the difference in means.

## Things not to do

- Do not modify Yiqin's review-queue artifact
  (https://claude.ai/artifact/FoSwySdjcy78Y9XEkfd9KC). Read it only.
- Do not name Yiqin's two cases, or their specifics, in a prompt: they are the
  validation set.
- Do not show the case run the agent's issues or the judge's calls, and do not
  show the issue run the judge's score, reasoning or evidence, or the agent's
  probability.
- Commit as `ye-sylvette-sun`; the repo stores line endings as written
  (`.gitattributes: * -text`), so do not let an editor or script convert CRLF
  files to LF.

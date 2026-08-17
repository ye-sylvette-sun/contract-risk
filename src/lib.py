"""Shared machinery for the dataset build.

Five things every step needs:

* where the inputs live,
* how to make ONE stateless Claude call whose answer is forced to a schema,
* `numbered()` — how a document is shown to the model,
* `CLAUSE` — the single definition of what a clause is and how to point at one,
  injected into both prompts so neither can hold a copy that drifts,
* `locate()` — the only route by which a model's answer becomes dataset text.

The model does not write clause text. It returns a coarse line window plus a
short verbatim anchor at each end of the clause; `locate()` matches those
anchors against the file, snaps the boundary to where they actually are, and
slices the text out of the file. Nothing the model writes is carried into the
dataset — the anchors only say *where*.

See docs/DATASET.md.
"""

import bisect
import difflib
import json
import os
import re
import time
from pathlib import Path

import anthropic
import httpx

MODEL = "claude-opus-5"
# The model's own ceiling, read from GET /v1/models/claude-opus-5
# (`max_tokens: 128000`), not a self-imposed budget. It covers thinking AND the
# response together, and only what is actually produced is billed — so there is
# nothing to gain by setting it lower. It matters most in the experiments, where
# every provision of a contract is judged in ONE call and a batch that runs past
# the ceiling is not usefully truncated, it is lost after being paid for.
MAX_OUTPUT = 128_000
MIN_INPUT_TOKENS = 200       # cover sheets and stamps: not worth a call
MAX_INPUT_TOKENS = 900_000   # 1M context less the output budget, with margin

ANCHOR_WORDS = 8             # words copied verbatim from each end of a clause
ANCHOR_MATCH = 0.75          # below this, the clause is not where it was said
SLACK = 5                    # lines either side of the claimed window to search
FURNITURE = 20               # a source line this short may be a stamp, not text

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "prompts"
OUT = ROOT / "output"
LOGS = OUT / "llm_logs"
CONTRACTS = OUT / "contracts"
OPINIONS = OUT / "opinions"


# The bulk inputs. Too large for GitHub, so they are gitignored and provided
# separately, but they live inside the repo like everything else — every path in
# this file is relative to ROOT and nothing is read from outside it.
DATA = ROOT / "data"                  # Westlaw headnotes, opinions, the linking sheet

# The contract text comes from the Contract-Risk repo, which already downloaded
# the filings, OCR'd them (`ocrmypdf --force-ocr`), identified which file holds
# which agreement, and sliced each one down to the contract's own lines.
#
# That slice is a VERBATIM line range — their extract_contracts.py has the model
# return line numbers and cuts the lines itself, for the same reason this
# pipeline does. So the text is their OCR output unedited, which is what makes it
# usable here: an LLM chose the boundaries, but no LLM wrote the words.
# Independently checked — all 205 re-sliceable extractions are byte-identical to
# their source at the recorded spans. See docs/DATASET.md §3, step 0.
SOURCE = ROOT / "contract_risk" / "generated" / "corpus" / "contracts"
EXTRACTED = SOURCE / "extracted_contracts"    # <case>__<agreement>.txt
EXTRACTION = SOURCE / "contract_extraction.csv"   # where each slice came from
CHECK = SOURCE / "contract_check.csv"             # is it the right contract, readable?

_env = ROOT / ".env"
if _env.is_file():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        if "=" in _line and not _line.lstrip().startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())


# ---------------------------------------------------------------- taxonomy --
# Westlaw Key Number -> (key label, taxonomy code, what the key is about).
# Keys are the folder names under data/wl-headnotes-parsed/.
KEYS = {
    "143(2)": ("k143(2)", "1.1", "whether the clause is AMBIGUOUS on its face"),
    "152": ("k152", "1.1", "whether the clause's WORDING is unclear or open to interpretation"),
    "159": ("k159", "1.1", "whether a PARTICULAR WORD OR PHRASE has a disputed meaning"),
    "157": ("k157", "1.2", "whether a MISTAKE in writing, grammar or spelling affects meaning"),
    "158": ("k158", "1.2", "whether PUNCTUATION creates uncertainty about meaning"),
    "156": ("k156", "1.3", "whether GENERAL vs SPECIFIC words create a scope problem"),
    "155": ("k155", "1.3", "whether the ambiguity is CONSTRUED AGAINST THE DRAFTER"),
    "162": ("k162", "2.1", "whether the clause CONFLICTS with another provision"),
    "143-5": ("k143.5", "2.2", "whether meaning is in TENSION when the contract is read AS A WHOLE"),
    "147(3)": ("k147(3)", "2.2", "whether the clause requires CONSTRUING THE WHOLE CONTRACT TOGETHER"),
    "161": ("k161", "2.2", "the standalone effect of this SEPARATE CLAUSE"),
    "160": ("k160", "2.3", "whether a RECITAL conflicts with or controls the operative terms"),
}

TYPES = {
    "1.1": "Lexical ambiguity or vagueness — a word or phrase in the clause is "
           "susceptible to more than one reasonable reading on its face.",
    "1.2": "Mechanical error — a mistake in writing, grammar, spelling or "
           "punctuation that affects what the clause means.",
    "1.3": "General-vs-specific / list-scope problem — a catch-all or general "
           "term sits against enumerated specifics (ejusdem generis, expressio "
           "unius), making the clause's reach unclear.",
    "2.1": "Conflicting clauses — this clause cannot be squared with another "
           "clause of the same contract.",
    "2.2": "Whole-contract coherence — the clause's meaning only comes out (or "
           "falls apart) when the contract is read as a whole.",
    "2.3": "Recitals vs operative text — a recital conflicts with, or is argued "
           "to control, the operative terms.",
}

KEY_BY_LABEL = {label: (folder, code, about)
                for folder, (label, code, about) in KEYS.items()}


def risk_lines(key_labels):
    """The risk types a case was selected under, as prompt text."""
    out = []
    for label in key_labels:
        _, code, about = KEY_BY_LABEL[label]
        out.append(f"  [{code}] Westlaw key {label} — {about}.\n"
                   f"        {TYPES[code]}")
    return "\n".join(out)


def codes_of(case):
    """The taxonomy codes a case was filed under. The label, never a model's."""
    return sorted({KEY_BY_LABEL[k][1] for k in case["keys"]})


# ------------------------------------------------------------------- names --
def cite_id(citation):
    """44 F.Supp.3d 736 -> 44FSupp3d736 (a filename-safe case id)."""
    return re.sub(r"[^A-Za-z0-9]", "", citation)


def slug(text, words=4):
    """Short lowercase identifier from a human-readable name."""
    parts = re.sub(r"[^A-Za-z0-9 ]", " ", text).lower().split()
    return "_".join(parts[:words]) or "unnamed"


# ------------------------------------------------------------ the clause ----
# One definition, injected into both prompts as {clause_def} by prompt(). What
# a clause IS and how to point at one live here; WHICH clauses a step wants —
# the ones the court construed, or all of them — stays in each prompt.
CLAUSE = f"""\
### What a clause is

- A clause states an obligation, right, grant, limitation, condition or
  definition.
- These are **not** clauses, even when they carry a number:
  - a heading standing alone;
  - a title page, or a table of contents;
  - a signature, notary or attestation block;
  - an exhibit index.

### Where a clause starts and ends

- Take the **smallest unit that states a complete obligation, right, grant,
  limitation, condition or definition on its own**, together with its heading.
- Not a fragment of one, and not two of them run together.
- Report a clause once, under the boundaries the contract gives it, even when
  two disputed phrases sit inside it.

**The test, when a passage has parts.** Read one part on its own, without the
words that introduce it. If it states a complete obligation, right, limitation
or definition **by itself**, it is a clause and you must report it separately.
If it reads as a sentence fragment, it is not — the passage is one clause and
the parts stay inside it.

The parts may be labelled `(a) (b) (c)`, `A. B. C.`, `1. 2. 3.`, `(i) (ii)
(iii)` — **or carry no label at all.** A run of short titled blocks, each a
heading on its own line ending in a colon and followed by its own text, is the
same thing as a numbered list and is split the same way. Neither the label nor
its absence tells you anything; only the test does.

Three worked examples, because this is the judgement most often got wrong:

- `The Insurer may require that counsel 1) have minimum qualifications; 2)
  maintain errors and omissions coverage; 3) be located near the jurisdiction.`
  → **ONE clause.** "have minimum qualifications" is a fragment; it means
  nothing without "The Insurer may require that counsel".
- `Subsections C., D., E. and F. are hereby deleted and replaced with the
  following: C. The most the Insurer shall pay ... D. The Aggregate Limit shall
  be ... E. With respect to Coverage B ... F. ...`
  → **FOUR clauses.** Each of C, D, E and F states a complete limit on its own.
  The sentence that introduces them is an editing instruction, not a clause.
- ```
  6. SERVICE FEES AND BILLING METHODS
      ... general fee text ...
  Monthly Payment Subscriptions:
      ... how monthly renewal works ...
  CANCELING YOUR SUBSCRIPTION:
      ... the deadline and the address to write to ...
  ```
  → **THREE or more clauses, one per titled block.** `CANCELING YOUR
  SUBSCRIPTION` states, on its own, when and how a member must cancel. That it
  carries no number changes nothing.

### One heading can cover two unrelated clauses

A section heading is a label the drafter chose, not a promise that everything
beneath it is one provision. Where a single numbered section runs two subjects
that have nothing to do with each other, report them separately.

For instance a section headed `NOTICE` that first says how notices between the
parties are served, and then sets out a copyright-infringement reporting
procedure with its own designated agent and its own list of required contents,
is **two clauses**. They share a heading only because both happen to involve
notifying someone.

### How to point at a clause

Every document is shown to you with a line number and a `│` at the start of
every line. Line numbers are not part of the text; they are how you point at it.

For each clause, return four locating fields:

- `start_line` — the first line the clause occupies.
- `end_line` — the last line the clause occupies.
- `head` — its first {ANCHOR_WORDS} words.
- `tail` — its last {ANCHOR_WORDS} words.

Notes on those four:

- If the whole clause is shorter than {2 * ANCHOR_WORDS} words, put all of it
  in `head` and leave `tail` empty.
- Count words as the scan breaks them, not as they would read if spelled
  correctly.
- A line may hold more than one clause — the OCR often runs lettered sub-clauses
  together on a single line. Give each sub-clause the same line range and let
  the anchors tell them apart.
- The line range is a coarse locator; the anchors are the boundary. An
  off-by-a-few range is corrected from the anchors, so do not agonise over it.
  A wrong anchor cannot be corrected by anything.

### COPY THE ANCHORS EXACTLY AS THE SCAN SHOWS THEM

`head` and `tail` are not quotations to be tidied. They are how the pipeline
finds the clause in the file, and they are matched against the raw scan.

- Do not correct a spelling.
- Do not rejoin a word the scanner split across a line break.
- Do not drop a stray pipe, a page number or a Bates stamp sitting among the
  words.
- Do not restore a word you believe the scanner lost.
- Do not add `[sic]`, `[illegible]`, or brackets of any kind.

Where the scan reads `givr. thr. rlr.filulting pilrty`, write
`givr. thr. rlr.filulting pilrty`. An anchor you have improved will not match,
and the clause is lost.
"""


# ------------------------------------------------------------------- text ---
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
# Formatting markup only. A blanket <[^>]*> would eat <name@example.com>, which
# OCR'd correspondence is full of and which is real text.
_TAG = re.compile(r"</?(?:b|i|u|em|strong|sup|sub|span|font|br|input)\b[^>]*/?>",
                  re.I)


def strip_ocr(raw):
    """Drop page markers, front-matter and figures.

    Run once, before anything else. Line numbers index this text, the model sees
    this text, and output/contracts/<cid>.md is written from it — so a line
    number can never mean two things.

    Most of this is a no-op on the professor's OCR, which is plain text with no
    markup: the markers and figure blocks below are the Datalab Marker artifacts
    this pipeline used to read. It stays because it is the single place where the
    text a line number means is fixed, and because a `.txt` that happens to carry
    an HTML tag should still be handled the same way. Docket stamps are NOT
    removed here — a stamp sits between two lines of a clause, so removing it
    would shift every line number after it. `normalise()` drops it from the
    extracted span instead, once the line numbers no longer matter.
    """
    text = "\n".join(ln for ln in raw.splitlines()
                     if not ln.strip().startswith(("----- Page ", "- source:",
                                                   "- pages:")))
    lines = _TAG.sub("", text).split("\n")
    out = list(lines)

    # Marker writes every figure three times: the markdown itself, a paragraph
    # describing the picture, then the alt text repeated verbatim. None of it is
    # text and no clause contains it. The repeated alt text is what anchors
    # this: nothing is removed without finding it. Lines are BLANKED, never
    # removed, so line numbers stay stable.
    for i, line in enumerate(lines):
        alts = {" ".join(a.split()) for a in _IMAGE.findall(line) if a.strip()}
        if not alts:
            continue
        out[i] = _IMAGE.sub(" ", line)
        run, j = [], i + 1
        while j < len(lines) and len(run) < 3:
            if lines[j].strip():
                run.append(j)
                if " ".join(lines[j].split()) in alts:
                    for k in run:
                        out[k] = ""
                    break
            j += 1
    return "\n".join(out)


def strip_pdf_text(raw):
    """The same job as strip_ocr(), for OCR'd plain text rather than markdown.

    Two input formats, two functions, so neither drifts into handling the
    other's furniture. This one blanks the CM/ECF header stamped across the top
    of every page of a filing (`_DOCKET`), and turns the form feeds the PDF text
    layer leaves behind into spaces.

    Lines are BLANKED, never removed, exactly as in strip_ocr(): line numbers
    have to index the same text the model sees and the same text
    output/contracts/<cid>.md is written from, or a line number means two
    different things.

    It deliberately does NOT touch bare page numbers or Bates stamps, though
    `normalise()` knows both. Those are dropped from an extracted span only, so
    the contract file keeps them and `source_span` offsets stay meaningful. The
    docket header is different in kind: it is the only furniture long enough to
    swallow a line of a clause in the model's view of the document, which is
    what makes it worth removing before the model reads rather than after.
    """
    out = []
    for line in raw.replace("\x0c", " ").split("\n"):
        s = line.strip(" |*_`#>~\t-.")
        out.append("" if s and _DOCKET.fullmatch(s) else line)
    return "\n".join(out)


def numbered(text):
    """How every document a step must point into is shown to the model."""
    return "\n".join(f"{i:5d}│{ln}"
                     for i, ln in enumerate(text.split("\n"), 1))


# ---------------------------------------------------------- normalisation ---
# A page number or a Bates stamp sitting on its own line between two lines of a
# clause. The whole line must be one and it must be short. Both patterns are
# labelled: a bare number is NOT one of them, because a lone `1993` or `5,000`
# on its own line is as likely to be a flattened table cell as a page number,
# and dropping a figure the contract states is the one thing this pipeline must
# never do. A stray page number left in the text is the cheaper mistake.
# This is the ONLY thing dropped from an extracted span. docs/DATASET.md §2.
_STAMP = re.compile(
    r"p(?:age)?\.?\s*\d{1,4}(?:\s*of\s*\d{1,4})?"   # p. 3  /  Page 3 of 12
    r"|[A-Za-z][\w&.\-]{0,20}[ \-_]0\d{3,}",        # Bates: ROADLINK 00066
    re.I)

# One part of the CM/ECF header stamped across the top of every page of a filing:
#
#   Case 2:15-cv-01243-SD Document 1-1 Filed 03/11/15 Page 1 of 20
#
# The professor's OCR keeps these, where Datalab's layout model used to drop
# them, so the pipeline has to. They are too long for the FURNITURE cap above and
# nothing in _STAMP matches them, so they are their own rule.
#
# Every district words the stamp differently and the OCR mangles it further, so
# no single spelling is written down: the parts are listed and any TWO of them
# make a stamp. `[il1]` in the PageID part is not a typo — OCR reads its capital
# I as a lowercase l or a 1, and `PagelD` is the commonest form in this corpus.
_PART = (
    r"\s*(?:case|in\sre)?[:\s]*\d{1,2}[:\-]\d{2}[\-\s]?[a-z]{0,3}[\-\s]?\d+"
    r"[\w\s.:\-]{0,24}?"                              # 2:15-cv-01243-ACK-BMK
    r"|\s*doc(?:ument)?i?\.?\s*#?:?\s*[\d\-]*"        # Document 1-1 / Doc #: 45
    r"|\s*(?:date\s+)?filed[:.]?\s*[\d/.]+"           # Filed 03/11/15
    r"|\s*entered(?:\s+on\s+\w+\s+docket)?[:.]?[\d/.\s:]*"
    r"|\s*entry\s+number\s*\d*"                       # Entry Number 45
    r"|\s*desc\b[\w\s]{0,40}"                         # Desc Main Document
    r"|\s*usdc\s+\w+"                                 # USDC Colorado
    r"|\s*pg?[:.]?\s*\d+\s*of\s*\d+"                  # Page 1 of 20
    r"|\s*page[:.]?\s*\d+(?:\s*of\s*\d+)?"            # Page: 1 of 20
    r"|\s*page\s*[il1]?\s*d?\s*#?[:.]?\s*\d*"         # PageID #: 123 / PagelD
)
_DOCKET = re.compile(rf"(?:{_PART}){{2,}}", re.I)

# The publisher's footer printed at the foot of a policy form. Unlike everything
# else here it is removed INLINE, because the OCR runs it into the middle of a
# text line rather than leaving it alone on one — so `_stamp`'s whole-line
# fullmatch, which is what makes the rules above safe, cannot reach it.
#
# Removing text from the middle of a line is the dangerous direction, so the
# pattern is deliberately narrow: a form id and its revision date, and the OCR
# junk between a copyright year and `Page N of M` ONLY when the whole footer is
# present. An earlier version allowed that junk after a bare form id and ate the
# word `Premises` out of `Policy Form No. PF-27556c (11/40) Premises Pollution
# Liability III Insurance Policy` — the policy's own name. Measured over all 117
# contracts: 49 hits in 3 of them, and every word it takes is either part of the
# form id or the garbled copyright glyph (`fsa`, `Keg`, `Kes`).
_FOOTER = re.compile(
    r"[A-Z]{2,4}-\d{3,6}[a-z]?.{0,2}?\s*\(\d{1,2}/\d{2,4}\)"    # PF-27556c (11/10)
    r"(?:\s*©\s*\d{4}"                                     # © 2010
    r"(?:\s+\S{1,6})?"                                          # junk, only here
    r"\s*Page\s+\d{1,3}\s+of\s+\d{1,3})?"                       # Page 11 of 13
    r"|[A-Z]&[A-Z]\s+\d{4,6}-\d+:[\d.]*(?:\s*\|\s*\w{1,4})?")   # K&E 40763-1:1075

# A word the scanner split across a line break. The continuation must be
# lowercase and on the very next line, so a hyphenated compound at the end of a
# paragraph is left alone.
_HYPHEN = re.compile(r"(\w)-\n[ \t]*([a-z])")


def _stamp(line):
    """Is the WHOLE line page furniture?

    `fullmatch` is the safety property, and it is the whole reason this may be
    trusted on a corpus this size: the line is only dropped when there is
    nothing else on it. One word of contract text sharing the line makes the
    match fail and the line survives, stamp and all.

    Measured over the 282,326 lines of the extracted corpus: 5,248 lines (1.86%)
    are dropped, and not one of them holds a run of four English words. About
    1,000 stamps survive — OCR mangled past any rule (`Case 2:11-cv-9 Document 3
    y HigsiP 9/13 Page 1 of 8`). Leaving those in is the cheap direction to err.
    """
    s = line.strip(" |*_`#>~\x0c\t-.")
    if not s:
        return False
    return (len(s) <= FURNITURE and _STAMP.fullmatch(s) is not None) \
        or _DOCKET.fullmatch(s) is not None


def normalise(text):
    """Dataset text from a raw span: drop furniture, de-hyphenate, collapse space.

    Nothing else. No substitution, no number correction, no label mending: what
    the scan says is what the dataset carries. docs/DATASET.md §2.

    Order matters. Whole-line stamps go first so a word the scanner split
    *across* one still rejoins; the inline footer goes last, because it can
    itself span a line break and would otherwise be half-eaten.
    """
    kept = "\n".join(ln for ln in text.split("\n") if not _stamp(ln))
    return " ".join(_FOOTER.sub(" ", _HYPHEN.sub(r"\1\2", kept)).split())


# -------------------------------------------------------------- locating ---
_FOLD = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'",
                       "–": "-", "—": "-", " ": " ",
                       "*": " ", "_": " ", "`": " ", "#": " ", ">": " ",
                       "|": " ", "~": " "})


def _words(text):
    """Whitespace and markdown punctuation are not evidence of anything.

    A token with no letter or digit in it is a list bullet or a table rule, not
    a word. Punctuation *inside* a word is kept, because a comma can decide a
    case. An anchor is compared under this folding so it is not lost to a stray
    pipe the model did or did not copy.
    """
    return [w for w in text.translate(_FOLD).lower().split()
            if any(c.isalnum() for c in w)]


def _tokens(text):
    """Every word of `text` as (folded word, start, end) offsets into `text`.

    `_FOLD` maps each character to exactly one character, so an offset into the
    folded text is an offset into `text` — which is what turns an anchor match
    into a character span in the contract file.
    """
    return [(m.group().lower(), m.start(), m.end())
            for m in re.finditer(r"\S+", text.translate(_FOLD))
            if any(c.isalnum() for c in m.group())]


def _token_lines(win, toks, lo):
    """The file line each token of `win` sits on, `win` starting at line `lo`."""
    starts = [0] + [i + 1 for i, ch in enumerate(win) if ch == "\n"]
    return [bisect.bisect_right(starts, s) - 1 + lo for _, s, _ in toks]


def _best(want, toks, target):
    """The run of len(want) tokens in `toks` that best matches `want`.

    Returns (ratio, first, last + 1). Ties go to the run nearest `target` — the
    position the model claimed — so a clause whose opening words also appear
    elsewhere in the widened window snaps to the copy it actually named.
    """
    n = len(want)
    best = (-1.0, 0, 0)
    for i in range(max(1, len(toks) - n + 1)):
        run = [t[0] for t in toks[i:i + n]]
        r = difflib.SequenceMatcher(None, want, run, autojunk=False).ratio()
        if r > best[0] or (r == best[0]
                           and abs(i - target) < abs(best[1] - target)):
            best = (r, i, i + len(run))
    return best


def locate(text, start, end, head, tail):
    """Where in `text` the clause with these anchors actually is.

    Returns (record, None), or (None, why) when an anchor does not match. The
    record carries the character span the anchors snapped to, the line range
    that span covers, the match score, and the extracted text.

    The anchors do three jobs (docs/DATASET.md §2). They prove the range is
    real:
    an anchor matching nothing near the claimed window means the model pointed
    at the wrong place. They repair the range: the window is widened by SLACK
    lines on each side and the boundary is snapped to where the anchor is, so a
    miscounted line is corrected rather than fatal. And they cut inside a line,
    which is what separates two lettered sub-clauses the OCR ran together.

    ANCHOR_MATCH is a locating tolerance, not a text tolerance — the text comes
    from the file regardless of how well the anchor scored. It exists because a
    model copying eight words out of damaged OCR will occasionally slip a
    character, and that must not cost a clause.
    """
    lines = text.split("\n")
    if not (isinstance(start, int) and isinstance(end, int)):
        return None, "line numbers are not integers"
    if not 1 <= start <= end <= len(lines):
        return None, f"line range {start}-{end} is outside the file (1-{len(lines)})"

    want_head, want_tail = _words(head), _words(tail)
    if not want_head:
        return None, "empty head anchor"

    lo, hi = max(1, start - SLACK), min(len(lines), end + SLACK)
    base = sum(len(ln) + 1 for ln in lines[:lo - 1])
    win = "\n".join(lines[lo - 1:hi])
    toks = _tokens(win)
    if not toks:
        return None, f"lines {lo}-{hi} hold no words"
    at = _token_lines(win, toks, lo)

    head_at = next((i for i, n in enumerate(at) if n >= start), 0)
    h_score, h_i, h_j = _best(want_head, toks, head_at)
    if h_score < ANCHOR_MATCH:
        return None, (f"head anchor matches lines {lo}-{hi} at only "
                      f"{h_score:.2f}: {head[:60]!r}")

    if want_tail:
        # The tail is searched from the head onward, so it can never land
        # before it, and the earliest best-scoring run wins — over-capture is
        # the failure this design cannot otherwise see (docs/DATASET.md §6).
        tail_at = max((i for i, n in enumerate(at) if n <= end),
                      default=len(toks) - 1)
        t_score, _t_i, t_j = _best(want_tail, toks[h_i:],
                                   max(0, tail_at - len(want_tail) + 1 - h_i))
        if t_score < ANCHOR_MATCH:
            return None, (f"tail anchor matches lines {lo}-{hi} at only "
                          f"{t_score:.2f}: {tail[:60]!r}")
        last, score = h_i + t_j - 1, min(h_score, t_score)
    else:
        last, score = h_j - 1, h_score

    s0, s1 = base + toks[h_i][1], base + toks[last][2]
    extracted = normalise(text[s0:s1])
    if not extracted:
        return None, f"characters {s0}-{s1} are empty once normalised"
    return {"lines": [_line_at(text, s0), _line_at(text, s1 - 1)],
            "span": [s0, s1], "score": round(score, 3),
            "text": extracted}, None


def _line_at(text, pos):
    return text.count("\n", 0, pos) + 1


def window(lines, start, end):
    """The raw source of a line range — how opinion_comment is extracted."""
    return "\n".join(lines[start - 1:end])


def overlaps(a, b):
    """Do two line windows share a line? The positive/negative test."""
    return a[0] <= b[1] and b[0] <= a[1]


# ------------------------------------------------------------------ calls ---
_client = None


def client():
    """Built on first use, so the steps that need no model also need no key."""
    global _client
    if _client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("ANTHROPIC_API_KEY is not set — put it in .env "
                             "(see .env.example)")
        _client = anthropic.Anthropic()
    return _client


def n_tokens(text):
    """Claude's own count, from the metering endpoint — no inference, no tokens
    charged. A text shorter than MIN_INPUT_TOKENS *characters* cannot be that
    many tokens, since no token is shorter than one character, so the trivial
    cases skip the round trip."""
    if len(text) < MIN_INPUT_TOKENS:
        return len(text)
    return client().messages.count_tokens(
        model=MODEL, messages=[{"role": "user", "content": text}]).input_tokens


def out_of_bounds(text):
    """Why this input cannot be sent, or None."""
    n = n_tokens(text)
    if n < MIN_INPUT_TOKENS:
        return f"{n} tokens, under the {MIN_INPUT_TOKENS}-token floor"
    if n > MAX_INPUT_TOKENS:
        return f"{n:,} tokens, over the {MAX_INPUT_TOKENS:,}-token ceiling"
    return None


SECTIONS = ("SYSTEM", "DOCUMENT", "INSTRUCTIONS", "TASK")

RETRIES = 4          # attempts after the first, on a transient server error
BACKOFF = 20         # seconds before the first retry, doubling thereafter


def _stream(name, model, effort, p, more=()):
    """One request, retried through transient server errors.

    The SDK retries a request that fails before the stream opens. It cannot
    retry one that fails DURING the stream, which is where these land: a call
    that has been running for ten minutes raises `overloaded_error` from inside
    the iterator and the whole answer is lost. On a run of sixty long calls that
    is close to certain to happen at least once, so it is handled here rather
    than left to kill the run.

    Only server-side and connection faults are retried. A 400 — a bad schema, an
    input over the ceiling — is deterministic and is raised immediately, because
    retrying it would just spend four more times as long failing.

    `httpx.HTTPError` is caught alongside the SDK's own exceptions and is not
    redundant. The SDK maps a transport failure to `APIConnectionError` only
    while it owns the request; once the stream is open the bytes are pulled
    through httpx directly, and a socket dropped mid-answer surfaces as a raw
    `httpx.ReadError` that the SDK never sees. One killed a 64-call run at call
    37 with `[WinError 10054] An existing connection was forcibly closed`.
    """
    body = {
        "model": model,
        "max_tokens": MAX_OUTPUT,
        "system": p["SYSTEM"],
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": p["DOCUMENT"]},
            {"type": "text", "text": p["INSTRUCTIONS"]},
            {"type": "text", "text": p["TASK"]},
        ]}] + list(more),
        "output_config": {"effort": effort,
                          "format": {"type": "json_schema",
                                     "schema": schema(name)}},
    }
    for attempt in range(RETRIES + 1):
        try:
            with client().messages.stream(**body) as stream:
                return stream.get_final_message()
        except (anthropic.APIStatusError, anthropic.APIConnectionError,
                httpx.HTTPError) as e:
            status = getattr(e, "status_code", None)
            if status is not None and status < 500 and status != 429:
                raise
            if attempt == RETRIES:
                raise
            wait = BACKOFF * 2 ** attempt
            print(f"  ! {type(e).__name__}"
                  f"{f' {status}' if status else ''}, retrying in {wait}s "
                  f"({attempt + 1}/{RETRIES})")
            time.sleep(wait)


def prompt(name, **fields):
    """prompts/<name>.md -> its four sections, filled in.

    The template is split into sections BEFORE substitution, so a heading that
    happens to appear inside an OCR'd document can never be read as a section
    marker. `{clause_def}` is filled from CLAUSE unless the caller overrides it,
    so no prompt can hold a copy of the clause definition that drifts.
    """
    fields.setdefault("clause_def", CLAUSE)
    text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    parts = re.split(rf"^## ({'|'.join(SECTIONS)})\s*$", text, flags=re.M)
    sections = {parts[i]: parts[i + 1].strip() for i in range(1, len(parts), 2)}
    missing = [s for s in SECTIONS if s not in sections]
    if missing:
        raise SystemExit(f"prompts/{name}.md has no {', '.join(missing)} section")
    return {k: v.format(**fields) for k, v in sections.items()}


def schema(name):
    return json.loads((PROMPTS / f"{name}.schema.json").read_text(encoding="utf-8"))


def ask(name, call_id, effort="high", model=MODEL, log_as=None, more=(), **fields):
    """One stateless call. Returns the parsed answer, or None if it did not land.

    `more` continues the conversation instead of starting a new one: the turns
    given are appended after the first user message, so the model is asked its
    follow-up question with its own earlier answer in view. The one caller that
    needs this is the top-up, which asks for the provisions a first answer left
    out — and asking "you missed these" only means anything if the model can see
    what it already said.

    `log_as` names the log directory when it should not be the prompt's name.
    One prompt can serve two experiments — `prompts/exp3.md` holds the judging
    criteria that both the one-shot and the agentic run are given, word for word
    — but their logs are separate bodies of data and are named after the
    experiment, not the template they share.

    The document goes first and the instructions after it, so a rule sits next
    to the text it governs rather than tens of thousands of tokens above it.

    `model` defaults to MODEL and is overridden by exactly one caller: step 0b's
    layout screen, which asks a shallow question of a whole document and so runs
    on Sonnet. The two steps that decide what goes in the dataset do not choose
    their model — they get MODEL.

    `effort="high"`, raised from "medium". Both steps ask a judgement question,
    not a lookup: step 1 decides whether the opinion really shows a dispute over
    a clause, and step 2 decides where one clause ends and the next begins —
    which, on a section built from unnumbered titled blocks, is exactly the call
    the medium-effort runs got wrong. Every call at medium spent ZERO thinking
    tokens (measured: output was 100% JSON), so there was headroom to buy.

    It is not free: reasoning is billed as output, and step 2's output already
    dominates its cost. Watch `output_tokens` in the logs after any change here.

    NOTHING IS CACHED, deliberately. The document block used to carry a
    `cache_control` marker, on the reasoning that a second call over the same
    document would be charged at cache-read rates. No caller has such a second
    call: step 1 sends an opinion plus every contract of a case, step 2 sends one
    contract on its own, the experiments send one contract per call, and no two
    calls anywhere share a prefix. Measured over the first four real calls:
    54,310 tokens written to cache, ZERO read. Since a cache write is billed at
    1.25x the base input rate, the marker was a flat 25% surcharge on every
    document token in exchange for nothing — about $13 over a full 68-case run.
    Do not restore it without a prefix two calls actually share.
    """
    p = prompt(name, **fields)
    msg = _stream(name, model, effort, p, more)

    d = LOGS / (log_as or name)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{call_id}.json").write_text(json.dumps({
        "system": p["SYSTEM"], "document": p["DOCUMENT"],
        "instructions": p["INSTRUCTIONS"], "task": p["TASK"],
        "model": msg.model, "stop_reason": msg.stop_reason,
        "usage": msg.usage.model_dump(),
        "response": [b.model_dump() for b in msg.content],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    if msg.stop_reason == "refusal":
        print(f"  ! refused: {call_id}")
        return None
    if msg.stop_reason == "max_tokens":
        print(f"  ! truncated at {MAX_OUTPUT:,} output tokens: {call_id}")
        return None
    return json.loads(next(b.text for b in msg.content if b.type == "text"))


# ---------------------------------------------------------------- storage ---
def read_json(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> {path.relative_to(ROOT)}")

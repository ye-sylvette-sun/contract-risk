"""Shared machinery for the dataset build: paths, the taxonomy, one stateless
Claude call, `numbered()`, the `CLAUSE` definition both prompts share, and
`locate()`.

The model never writes clause text. It returns a coarse line window plus a short
verbatim anchor at each end; `locate()` matches those against the file, snaps
the boundary to where they actually are, and slices the text out of the file
itself. The anchors only say *where*. See docs/DATASET.md.
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
# The model's own ceiling, not a self-imposed budget: covers thinking and
# response together, and only what is produced is billed, so there is nothing to
# gain by lowering it.
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


# Bulk inputs: gitignored and provided separately, but linked inside the repo —
# every path here is relative to ROOT and nothing is read from outside it.
DATA = ROOT / "data"                  # Westlaw headnotes, opinions, the linking sheet

# Contract text comes from the Contract-Risk repo: filings downloaded, OCR'd
# (`ocrmypdf --force-ocr`), then cut to each agreement's own lines by a VERBATIM
# line range. An LLM chose the boundaries; no LLM wrote the words. All 205
# re-sliceable extractions are byte-identical to their source.
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
# Injected into both prompts as {clause_def}. What a clause IS and how to point
# at one live here; WHICH clauses a step wants stays in each prompt.
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
    """Drop page markers, front-matter and figures (Datalab Marker artifacts).

    Run once, before anything else: line numbers index this text, the model sees
    this text, and output/contracts/<cid>.md is written from it, so a line
    number can never mean two things. Docket stamps are NOT removed here — that
    would shift every line number after them; `normalise()` drops them from the
    extracted span instead.
    """
    text = "\n".join(ln for ln in raw.splitlines()
                     if not ln.strip().startswith(("----- Page ", "- source:",
                                                   "- pages:")))
    lines = _TAG.sub("", text).split("\n")
    out = list(lines)

    # Marker writes every figure three times: markdown, a description, then the
    # alt text verbatim. The repeated alt text anchors this — nothing is removed
    # without finding it. Lines are BLANKED, never removed, so line numbers hold.
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
    """strip_ocr()'s job for OCR'd plain text: blank the CM/ECF header
    (`_DOCKET`) and turn form feeds into spaces.

    Lines are BLANKED, never removed, as in strip_ocr(). Bare page numbers and
    Bates stamps are deliberately left alone — `normalise()` drops those from an
    extracted span, so the file keeps them and `source_span` stays meaningful.
    The docket header is the only furniture long enough to swallow a line of a
    clause in the model's view, which is why it goes before the model reads.
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
# A page number or Bates stamp alone on its line. Both patterns are LABELLED: a
# bare number is not one, because a lone `1993` is as likely to be a flattened
# table cell, and dropping a figure the contract states is the one thing this
# must never do. The only thing dropped from an extracted span.
_STAMP = re.compile(
    r"p(?:age)?\.?\s*\d{1,4}(?:\s*of\s*\d{1,4})?"   # p. 3  /  Page 3 of 12
    r"|[A-Za-z][\w&.\-]{0,20}[ \-_]0\d{3,}",        # Bates: ROADLINK 00066
    re.I)

# The CM/ECF header, e.g. `Case 2:15-cv-01243-SD Document 1-1 Filed 03/11/15
# Page 1 of 20`. Too long for FURNITURE and unmatched by _STAMP, so its own rule.
# Every district words it differently and OCR mangles it further, so no single
# spelling is written down: any TWO parts make a stamp. `[il1]` is not a typo —
# OCR reads the capital I of PageID as l or 1.
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

# A policy form's publisher footer. Removed INLINE, because OCR runs it into
# the middle of a text line where `_stamp`'s whole-line fullmatch cannot reach.
# Cutting mid-line is the dangerous direction, so the pattern is narrow: the
# junk after a copyright year is allowed ONLY when the whole footer is present.
# An earlier version allowed it after a bare form id and ate the word `Premises`
# out of a policy's own name. 49 hits across 3 of the 117 contracts.
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

    `fullmatch` is the safety property: one word of contract text sharing the
    line makes the match fail and the line survives, stamp and all. Over the
    corpus's 282,326 lines this drops 1.86%, none holding four English words;
    ~1,000 OCR-mangled stamps survive, which is the cheap direction to err.
    """
    s = line.strip(" |*_`#>~\x0c\t-.")
    if not s:
        return False
    return (len(s) <= FURNITURE and _STAMP.fullmatch(s) is not None) \
        or _DOCKET.fullmatch(s) is not None


def normalise(text):
    """Dataset text from a raw span: drop furniture, de-hyphenate, collapse space.

    Nothing else — what the scan says is what the dataset carries. Order
    matters: whole-line stamps first so a word split across one still rejoins,
    the inline footer last because it can itself span a line break.
    """
    kept = "\n".join(ln for ln in text.split("\n") if not _stamp(ln))
    return " ".join(_FOOTER.sub(" ", _HYPHEN.sub(r"\1\2", kept)).split())


# -------------------------------------------------------------- locating ---
_FOLD = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'",
                       "–": "-", "—": "-", " ": " ",
                       "*": " ", "_": " ", "`": " ", "#": " ", ">": " ",
                       "|": " ", "~": " "})


def _words(text):
    """Words, ignoring markdown punctuation. Punctuation INSIDE a word is kept —
    a comma can decide a case. Anchors are compared under this folding so one is
    not lost to a stray pipe the model did or did not copy."""
    return [w for w in text.translate(_FOLD).lower().split()
            if any(c.isalnum() for c in w)]


def _tokens(text):
    """Every word of `text` as (folded word, start, end).

    `_FOLD` is 1:1, so an offset into the folded text is an offset into `text` —
    which is what turns an anchor match into a character span in the file.
    """
    return [(m.group().lower(), m.start(), m.end())
            for m in re.finditer(r"\S+", text.translate(_FOLD))
            if any(c.isalnum() for c in m.group())]


def _token_lines(win, toks, lo):
    """The file line each token of `win` sits on, `win` starting at line `lo`."""
    starts = [0] + [i + 1 for i, ch in enumerate(win) if ch == "\n"]
    return [bisect.bisect_right(starts, s) - 1 + lo for _, s, _ in toks]


def _best(want, toks, target):
    """The run of len(want) tokens best matching `want` -> (ratio, first, last+1).

    Ties go to the run nearest `target`, the position the model claimed, so
    repeated opening words snap to the copy it actually named.
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

    Returns (record, None), or (None, why) if an anchor does not match. The
    anchors do three jobs: prove the range is real, repair it (the window is
    widened by SLACK lines and the boundary snapped to the anchor), and cut
    INSIDE a line, which is what separates sub-clauses the OCR ran together.

    ANCHOR_MATCH is a locating tolerance, not a text tolerance — the text comes
    from the file regardless of the score.
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
        # Searched from the head onward, so the tail can never land before it
        # and the earliest best-scoring run wins — over-capture is the failure
        # this design cannot otherwise see.
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
    """Claude's own count, from the metering endpoint. A text shorter than
    MIN_INPUT_TOKENS characters cannot be that many tokens, so it skips the
    round trip."""
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

    The SDK retries a failure BEFORE the stream opens but cannot retry one
    during it — a ten-minute call raising `overloaded_error` from inside the
    iterator loses the whole answer. Only server and connection faults are
    retried; a 400 is deterministic and raised at once.

    `httpx.HTTPError` is not redundant: once the stream is open the bytes come
    through httpx directly, and a socket dropped mid-answer surfaces as a raw
    `httpx.ReadError` the SDK never sees. One killed a 64-call run at call 37.
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

    Split into sections BEFORE substitution, so a heading inside an OCR'd
    document can never be read as a section marker.
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

    `more` continues the conversation instead of starting a new one — the top-up
    needs this, since "you missed these" only means anything if the model can
    see what it already said. `log_as` names the log directory when one prompt
    serves two experiments whose logs must stay separate.

    The document goes first and the instructions after it, so a rule sits beside
    the text it governs rather than tens of thousands of tokens above it.

    `effort="high"`, raised from "medium": both steps ask a judgement, and every
    call at medium spent ZERO thinking tokens, so there was headroom to buy.
    Reasoning is billed as output — watch `output_tokens` after changing it.

    NOTHING IS CACHED, deliberately. No two calls share a prefix (measured:
    54,310 tokens written to cache, ZERO read), and a cache write costs 1.25x
    base input — a flat 25% surcharge for nothing. Do not restore the marker
    without a prefix two calls actually share.
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

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

See docs/DESIGN.md.
"""

import bisect
import difflib
import json
import os
import re
from pathlib import Path

import anthropic

MODEL = "claude-opus-5"
MAX_OUTPUT = 64_000          # covers thinking + response
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
BLOOMBERG = ROOT / "bloomberg"        # the filed PDFs, as downloaded
DATALAB = ROOT / "bloomberg_datalab"  # those PDFs after Datalab Marker OCR

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
  - Where a numbered section is a list of lettered sub-clauses, each sub-clause
    is a clause and the section is not.
  - Where a section is not subdivided, the section is the clause.
- Not a fragment of one, and not two sections run together.
- Report a clause once, under the boundaries the contract gives it, even when
  two disputed phrases sit inside it.

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
    """Drop Datalab page markers, front-matter and figures.

    Run once, before anything else. Line numbers index this text, the model sees
    this text, and output/contracts/<cid>.md is written from it — so a line
    number can never mean two things.
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
# This is the ONLY thing dropped from an extracted span. docs/DESIGN.md §4.
_STAMP = re.compile(
    r"p(?:age)?\.?\s*\d{1,4}(?:\s*of\s*\d{1,4})?"   # p. 3  /  Page 3 of 12
    r"|[A-Za-z][\w&.\-]{0,20}[ \-_]0\d{3,}",        # Bates: ROADLINK 00066
    re.I)

# A word the scanner split across a line break. The continuation must be
# lowercase and on the very next line, so a hyphenated compound at the end of a
# paragraph is left alone.
_HYPHEN = re.compile(r"(\w)-\n[ \t]*([a-z])")


def _stamp(line):
    return bool(s := line.strip(" |*_`#>~")) and len(s) <= FURNITURE \
        and _STAMP.fullmatch(s) is not None


def normalise(text):
    """Dataset text from a raw span: drop stamps, de-hyphenate, collapse space.

    Nothing else. No substitution, no number correction, no label mending: what
    the scan says is what the dataset carries. docs/DESIGN.md §4.

    Stamps go first so a word the scanner split *across* one still rejoins.
    """
    kept = "\n".join(ln for ln in text.split("\n") if not _stamp(ln))
    return " ".join(_HYPHEN.sub(r"\1\2", kept).split())


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

    The anchors do three jobs (docs/DESIGN.md §2). They prove the range is real:
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
        # the failure this design cannot otherwise see (docs/DESIGN.md §7).
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


def ask(name, call_id, effort="medium", **fields):
    """One stateless call. Returns the parsed answer, or None if it did not land.

    The document goes in its own cache-marked block, and the instructions come
    after it, so a second call over the same document is charged at cache-read
    rates and the instructions are the last thing the model reads.
    """
    p = prompt(name, **fields)
    with client().messages.stream(
        model=MODEL,
        max_tokens=MAX_OUTPUT,
        system=p["SYSTEM"],
        messages=[{"role": "user", "content": [
            {"type": "text", "text": p["DOCUMENT"],
             "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": p["INSTRUCTIONS"]},
            {"type": "text", "text": p["TASK"]},
        ]}],
        output_config={"effort": effort,
                       "format": {"type": "json_schema", "schema": schema(name)}},
    ) as stream:
        msg = stream.get_final_message()

    d = LOGS / name
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

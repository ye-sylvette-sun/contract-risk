"""Shared machinery for the dataset build.

Four things every step needs:

* where the inputs live,
* how to make ONE stateless Claude call whose answer is forced to a schema,
* `numbered()` — how a document is shown to the model,
* `verify()` — the only route by which model output becomes dataset text.

Every step that must produce source text returns a line window plus that text
with OCR damage repaired. `verify()` checks the two against each other: the text
must align word-for-word with the window, differing only by typo-scale
substitutions, with no word added or removed. A hallucinated clause has no
alignment; `shall not` -> `shall` is a deletion and is rejected outright.

See docs/PIPELINE.md §1.
"""

import difflib
import json
import os
import re
from collections import Counter
from pathlib import Path

import anthropic

MODEL = "claude-opus-5"
MAX_OUTPUT = 64_000          # covers thinking + response
MIN_INPUT_TOKENS = 200       # cover sheets and stamps: not worth a call
MAX_INPUT_TOKENS = 900_000   # 1M context less the output budget, with margin

MAX_EDIT = 2                 # Levenshtein budget per substituted run of words
RETRY_LINES = 5              # widen the window once by this much on each side
FURNITURE = 20               # a whole source line this short may be skipped

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
    "143-5": ("k143.5", "2.2", "whether meaning is in TENSION when the instrument is read AS A WHOLE"),
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
    "2.1": "Conflicting clauses — this provision cannot be squared with another "
           "provision of the same instrument.",
    "2.2": "Whole-instrument coherence — the provision's meaning only comes out "
           "(or falls apart) when the instrument is read as a whole.",
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


# ------------------------------------------------------------------- names --
def cite_id(citation):
    """44 F.Supp.3d 736 -> 44FSupp3d736 (a filename-safe case id)."""
    return re.sub(r"[^A-Za-z0-9]", "", citation)


def slug(text, words=4):
    """Short lowercase identifier from a human-readable name."""
    parts = re.sub(r"[^A-Za-z0-9 ]", " ", text).lower().split()
    return "_".join(parts[:words]) or "unnamed"


# ------------------------------------------------------------------- text ---
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
# Formatting markup only. A blanket <[^>]*> would eat <name@example.com>, which
# OCR'd correspondence is full of and which is real text.
_TAG = re.compile(r"</?(?:b|i|u|em|strong|sup|sub|span|font|br|input)\b[^>]*/?>",
                  re.I)


def strip_ocr(raw):
    """Drop Datalab page markers and front-matter.

    Run once, before anything else. Line numbers index this text, the model sees
    this text, and output/contracts/<id>.md is written from it — so a line number
    can never mean two things.
    """
    text = "\n".join(ln for ln in raw.splitlines()
                     if not ln.strip().startswith(("----- Page ", "- source:",
                                                   "- pages:")))
    lines = _TAG.sub("", text).split("\n")
    out = list(lines)

    # Marker writes every figure three times: the markdown itself, a paragraph
    # describing the picture, then the alt text repeated verbatim. None of it is
    # text, no provision contains it, and leaving it in would force the model to
    # delete words to quote around it — which verify() rejects. The repeated alt
    # text is what anchors this: nothing is removed without finding it.
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


# ----------------------------------------------------------- verification ---
_FOLD = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'",
                       "–": "-", "—": "-", " ": " ",
                       "*": " ", "_": " ", "`": " ", "#": " ", ">": " ",
                       "|": " ", "~": " "})


def _words(text):
    """Whitespace and markdown punctuation are not evidence of anything.

    A token with no letter or digit in it is a list bullet or a table rule, not
    a word: the model drops them when it quotes a provision and it is right to.
    Punctuation *inside* a word is kept, because a comma can decide a case.
    """
    return [w for w in text.translate(_FOLD).lower().split() if any(c.isalnum() for c in w)]


_DIGITS = re.compile(r"\d+")


def _number_changed(was, now):
    """A repair may not alter a number.

    `$1,000` -> `$4,000` and `Section 12` -> `Section 13` are single-character
    edits that change what a clause means, so the distance budget alone does not
    catch them. Digits are compared only when BOTH sides have them: when one
    side has none the edit is a letter/digit OCR fix (`Sect1on` -> `Section`),
    which is exactly what repair is for. A rule, not a threshold.
    """
    a, b = _DIGITS.findall(was), _DIGITS.findall(now)
    return bool(a) and bool(b) and a != b


def _lev(a, b, cap):
    """Levenshtein distance, abandoned as soon as it exceeds `cap`."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def _is_furniture(line):
    """A source line the model may skip when quoting the text around it.

    Scanned exhibits carry stamps between the lines of a provision: fax headers
    (`Jan 15 08 09:36a`), page marks (`p.3`), Bates numbers (`Roadlink 00066`)
    and initialling (`P.D.`, `nr J.B.M.C.`). Each sits alone on a short line.
    A provision runs to hundreds of characters, so length separates them.

    The lowercase test is what keeps `not` or `may` from qualifying if the OCR
    ever breaks one onto its own line: stamps are dates, initials, names and
    numbers — capitalised or shorter than a word — while a word dropped from
    the middle of a sentence is lowercase.
    """
    s = line.strip()
    if not s or len(s) > FURNITURE:
        return False
    return not any(w.isalpha() and w.islower() and len(w) >= 3
                   for w in re.split(r"[^A-Za-z]+", s))


def _align(lines, text):
    """None if `text` is a typo-level copy of some stretch of `lines`."""
    flat = [(w, i) for i, line in enumerate(lines) for w in _words(line)]
    window = [w for w, _ in flat]
    owner = [i for _, i in flat]
    per_line = Counter(owner)          # words each source line contributes

    sm = difflib.SequenceMatcher(None, window, text, autojunk=False)
    blocks = [m for m in sm.get_matching_blocks() if m.size]
    if not blocks:
        return "shares no words with the window"

    # The window may over-capture — a line holding two sub-provisions belongs to
    # both — so trim it to the stretch the text matches, leaving room for the
    # text's own unmatched head and tail.
    head, tail = blocks[0], blocks[-1]
    lo = max(0, head.a - head.b)
    hi = tail.a + tail.size + (len(text) - tail.b - tail.size)
    window, owner = window[lo:hi], owner[lo:hi]

    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, window, text, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete" and _skippable(lines, owner, per_line, i1, i2):
            continue
        if tag != "replace":
            shown = " ".join((window[i1:i2] or text[j1:j2])[:8])
            verb = "dropped" if tag == "delete" else "added"
            return f"{verb} {max(i2 - i1, j2 - j1)} word(s): {shown!r}"
        was, now = " ".join(window[i1:i2]), " ".join(text[j1:j2])
        if _number_changed(was, now):
            return f"changed a number: {was[:60]!r} -> {now[:60]!r}"
        if _lev(was, now, MAX_EDIT) > MAX_EDIT:
            return f"rewrote {was[:60]!r} as {now[:60]!r}"
    return None


def _skippable(lines, owner, per_line, i1, i2):
    """Is this deletion nothing but whole lines of page furniture?

    Every word of every line it touches must fall inside the deletion — a
    deletion that cuts into a line whose rest was kept is a deletion from a
    sentence, and is rejected however short the line is.
    """
    touched = Counter(owner[i1:i2])
    return bool(touched) and all(
        touched[i] == per_line[i] and _is_furniture(lines[i]) for i in touched)


def verify(lines, start, end, text):
    """Is `text` lines start..end of `lines`, give or take OCR repair?

    Returns None when it verifies, otherwise the reason it does not. On failure
    the window is widened once by RETRY_LINES on each side and retried, because
    models miscount lines and an off-by-few should not cost a clause.
    """
    if not (isinstance(start, int) and isinstance(end, int)):
        return "line numbers are not integers"
    if not 1 <= start <= end <= len(lines):
        return f"line range {start}-{end} is outside the file (1-{len(lines)})"
    words = _words(text)
    if not words:
        return "empty text"

    fault = _align(lines[start - 1:end], words)
    if fault is None:
        return None
    lo, hi = max(1, start - RETRY_LINES), min(len(lines), end + RETRY_LINES)
    if (lo, hi) != (start, end):
        if _align(lines[lo - 1:hi], words) is None:
            return None
    return fault


def window(lines, start, end):
    """The raw source of a verified span — dataset column clause_text_raw."""
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


def prompt(name, **fields):
    """prompts/<name>.md -> the SYSTEM / DOCUMENT / TASK sections, filled in.

    The template is split into sections BEFORE substitution, so a heading that
    happens to appear inside an OCR'd document can never be read as a section
    marker.
    """
    text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    parts = re.split(r"^## (SYSTEM|DOCUMENT|TASK)\s*$", text, flags=re.M)
    sections = {parts[i]: parts[i + 1].strip() for i in range(1, len(parts), 2)}
    return {k: v.format(**fields) for k, v in sections.items()}


def schema(name):
    return json.loads((PROMPTS / f"{name}.schema.json").read_text(encoding="utf-8"))


def ask(name, call_id, effort="medium", **fields):
    """One stateless call. Returns the parsed answer, or None if it was refused.

    The document goes in its own cache-marked block ahead of the task, so a
    second call over the same document is charged at cache-read rates.
    """
    p = prompt(name, **fields)
    with client().messages.stream(
        model=MODEL,
        max_tokens=MAX_OUTPUT,
        system=p["SYSTEM"],
        messages=[{"role": "user", "content": [
            {"type": "text", "text": p["DOCUMENT"],
             "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": p["TASK"]},
        ]}],
        output_config={"effort": effort,
                       "format": {"type": "json_schema", "schema": schema(name)}},
    ) as stream:
        msg = stream.get_final_message()

    d = LOGS / name
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{call_id}.json").write_text(json.dumps({
        "system": p["SYSTEM"], "document": p["DOCUMENT"], "task": p["TASK"],
        "model": msg.model, "stop_reason": msg.stop_reason,
        "usage": msg.usage.model_dump(),
        "response": [b.model_dump() for b in msg.content],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    if msg.stop_reason == "refusal":
        print(f"  ! refused: {call_id}")
        return None
    return json.loads(next(b.text for b in msg.content if b.type == "text"))


# ---------------------------------------------------------------- storage ---
def read_json(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> {path.relative_to(ROOT)}")

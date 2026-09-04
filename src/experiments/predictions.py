"""Reading the judgment files the agent writes by hand.

Shared by the container (which decides whether a session still owes provisions)
and the host (which scores them), so the two cannot disagree about what counts
as judged.

A judgment is `{clause_id, issues: [{issue, type, prob}, ...]}`. The list
is what makes the salvage below harder than it was when a judgment held two
fixed probability fields: the issues are variable in number, and the free text
that can break the JSON now sits inside each of them.
"""
import json
import re

# One issue, recovered without parsing. Anchored on the NEXT key rather than on
# the closing quote of the current one, which is what makes it immune to the
# unescaped quotes the agent puts in `issue` when it quotes the contract.
# `issue` may be null, so the pattern takes either a string or a bare null.
ISSUE_RE = re.compile(
    r'"issue"\s*:\s*(?:"(?P<text>.*?)"|(?P<null>null))\s*,'
    r'\s*"type"\s*:\s*(?P<type>[12])\s*,'
    r'\s*"prob"\s*:\s*(?P<prob>[0-9.]+)', re.S)

# The same three fields written in the other order. The schema fixes an order
# for a structured-output call; an agent writing the file by hand does not
# always follow it, and an issue lost to field order is an issue paid for twice.
ISSUE_RE_ALT = re.compile(
    r'"type"\s*:\s*(?P<type>[12])\s*,'
    r'\s*"prob"\s*:\s*(?P<prob>[0-9.]+)\s*,'
    r'\s*"issue"\s*:\s*(?:"(?P<text>.*?)"|(?P<null>null))', re.S)

CLAUSE_ID_RE = re.compile(r'"clause_id"\s*:\s*"(.*?)"')


def _issues_in(chunk):
    """Every issue in one judgment's text, in the order they appear.

    Both field orders are tried and the results merged on position, so a file
    that mixes them does not silently lose half its issues.
    """
    found = {}
    for rx in (ISSUE_RE, ISSUE_RE_ALT):
        for m in rx.finditer(chunk):
            found[m.start()] = {
                "issue": None if m.group("null") else m.group("text"),
                "type": int(m.group("type")),
                "prob": float(m.group("prob")),
            }
    return [found[k] for k in sorted(found)]


def _f(x, default=0.0):
    try:
        return min(max(float(x), 0.0), 1.0)
    except (TypeError, ValueError):
        return default


def issues_of(judgment):
    """The judgment's issue list, cleaned: [(type, prob, text)].

    An entry missing a probability is dropped rather than guessed at, and a type
    outside 1/2 with it. `issue` null is meaningful and kept — it is how a
    model states a probability for a type it found no specific defect in.
    """
    out = []
    for it in (judgment or {}).get("issues") or []:
        if not isinstance(it, dict) or it.get("prob") is None:
            continue
        try:
            t = int(it.get("type"))
        except (TypeError, ValueError):
            continue
        if t in (1, 2):
            out.append((t, _f(it.get("prob")), it.get("issue")))
    return out


def probs_of(judgment):
    """(prob_type1, prob_type2) from an issue list.

    The STRONGEST issue of each type, not a sum: the probabilities are per issue
    and independent, so two weak issues do not add up to a likely dispute, and
    the question each panel asks is whether a court would construe the clause on
    account of ANY of them. A type with no entry scores 0 — which the prompt
    warns is a stronger claim than most answers mean, and why it asks for a
    null-text entry instead.

    Here rather than in either experiment, so the one-shot arm, the agent arm
    and the Spellbook scorer cannot disagree about what a probability is.
    """
    p = {1: 0.0, 2: 0.0}
    for t, prob, _text in issues_of(judgment):
        p[t] = max(p[t], prob)
    return p[1], p[2]


def valid(judgment):
    """Is this a judgment we can score?

    One usable issue is enough. A judgment whose every entry was dropped carries
    no probability for either type, and scoring it would mean inventing one.
    """
    if not isinstance(judgment, dict):
        return False
    if not str(judgment.get("clause_id", "")).strip():
        return False
    return any(isinstance(i, dict) and i.get("prob") is not None
               and i.get("type") in (1, 2, "1", "2")
               for i in judgment.get("issues") or [])


def salvage(text):
    """The judgments in one file, valid JSON or not.

    The agent eventually writes one that will not parse, usually by quoting the
    contract inside `issue` without escaping the quotes — that once cost 75
    judgments already paid for. A failed parse falls back to object-by-object,
    then issue-by-issue with the patterns above.

    Nothing is invented: a judgment no issue could be read out of is dropped.
    """
    try:
        data = json.loads(text)
        j = data.get("judgments") if isinstance(data, dict) else data
        if isinstance(j, list):
            return j, None
        return [], "no judgments list"
    except json.JSONDecodeError:
        pass

    # Split on the start of each judgment, which the schema pins down exactly.
    starts = [m.start() for m in re.finditer(r'\{\s*"clause_id"', text)]
    out, repaired = [], 0
    for i, s in enumerate(starts):
        chunk = text[s:starts[i + 1] if i + 1 < len(starts) else len(text)]
        try:
            out.append(json.loads(chunk.rstrip().rstrip(",").rstrip("]}").rstrip()
                                  if not chunk.rstrip().endswith("}") else chunk))
            continue
        except json.JSONDecodeError:
            pass
        m = CLAUSE_ID_RE.search(chunk)
        issues = _issues_in(chunk)
        if m and issues:
            out.append({"clause_id": m.group(1), "issues": issues})
            repaired += 1
    note = (f"invalid JSON; recovered {len(out)} judgment(s) object-by-object"
            + (f", {repaired} issue-by-issue" if repaired else ""))
    return out, note


def judgments_in(root):
    """{opaque clause id: judgment} across every predictions file in `root`.

    Opaque ids only; mapping back to dataset ids needs the gold, which the
    container does not have.
    """
    by_id, notes = {}, []
    for f in sorted(root.glob("predictions*.json")):
        judgments, note = salvage(f.read_text(encoding="utf-8", errors="replace"))
        if note:
            notes.append(f"{f.name}: {note}")
        for j in judgments:
            if valid(j):
                by_id[str(j["clause_id"]).strip()] = j
    return by_id, notes

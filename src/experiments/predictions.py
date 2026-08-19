"""Reading the judgment files the agent writes by hand.

Shared by the container (which decides whether a session still owes provisions)
and the host (which scores them), so the two cannot disagree about what counts
as judged.
"""
import json
import re

FIELD_RE = {
    "clause_id": re.compile(r'"clause_id"\s*:\s*"(.*?)"\s*,', re.S),
    "reasoning_cat1": re.compile(
        r'"reasoning_cat1"\s*:\s*"(.*?)"\s*,\s*"(?:reasoning_cat2|prob_)', re.S),
    "reasoning_cat2": re.compile(
        r'"reasoning_cat2"\s*:\s*"(.*?)"\s*,\s*"prob_', re.S),
    "prob_cat1": re.compile(r'"prob_cat1"\s*:\s*([0-9.]+)'),
    "prob_cat2": re.compile(r'"prob_cat2"\s*:\s*([0-9.]+)'),
}


def salvage(text):
    """The judgments in one file, valid JSON or not.

    The agent eventually writes one that will not parse, usually by quoting the
    contract inside a reasoning string without escaping the quotes — that once
    cost 75 judgments already paid for. A failed parse falls back to
    object-by-object, then field-by-field, with patterns anchored on the NEXT
    key, which is what makes them immune to unescaped quotes.
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
        got = {}
        for key, rx in FIELD_RE.items():
            m = rx.search(chunk)
            if m:
                got[key] = m.group(1)
        if "clause_id" in got and "prob_cat1" in got and "prob_cat2" in got:
            out.append(got)
            repaired += 1
    note = (f"invalid JSON; recovered {len(out)} judgment(s) object-by-object"
            + (f", {repaired} field-by-field" if repaired else ""))
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
            if not isinstance(j, dict):
                continue
            cid = str(j.get("clause_id", "")).strip()
            if cid and "prob_cat1" in j and "prob_cat2" in j:
                by_id[cid] = j
    return by_id, notes

"""Turn a pasted Spellbook risks-&-negotiation review into one JSON per contract.

Two steps, one pass, one output file:

  1. PARSE the pasted review into issues and locate each one in the contract.
  2. CLASSIFY every located issue against the two construction-risk categories
     the main experiment uses -- cat1, cat2 or neither -- with one LLM call.

    spellbook/risks_negotiation/output/<contract_id>/<party>.txt   (pasted in)
    spellbook/risks_negotiation/results/<contract_id>.json         (written)

Spellbook reviews a document ON BEHALF OF a named party, so every contract has
two reviews -- one per side -- and both are folded into the same output file
under `parties`.

## The pasted format

Each issue ends with a line `Apply` (or `Apply All (N)` where one issue stands
for N occurrences), and begins with its severity or category:

    Notable                       <- severity (Important/Notable/Minor) OR a
                                     proofread category (Definitions,
                                     Spelling + Grammar, ...)
    Released Parties definition should include independent contractors
    The definition of "Released Parties" includes officers, trustees, ...
    Revision                      <- or `Capitalize term(s)` / `Add definition`
    staff including employees, volunteers and interns, interns, and inde...
    Comment
    We added independent contractors to the Released Parties definition ...
    Counterparty / Internal / Show / Dismiss / Apply     <- UI furniture

## Locating an issue

The revision line is the only anchor into the document, and it is DIFF-MERGED:
the original words and Spellbook's replacement are run together with no
separator (`grosswillful or wanton misconduct`, `releaseRelease`, `theethe`,
`shal]shall`). An exact string match therefore fails on precisely the issues
that matter most.

So the match is fuzzy and word-based, in three steps: index the contract's
word 3-grams, vote for the region the snippet's 3-grams point at, then align
snippet against contract in that region with difflib and keep the span the
matching blocks cover. A snippet whose every word is an insertion scores low
and is left unlocated rather than placed somewhere plausible-looking.

## Classifying an issue

One call per contract: the contract goes up once and every located issue from
BOTH parties is judged against it, because Category 2 cannot be seen from a
single provision and a judgment on one side is better made with the other
side's findings in view. Issues with no location, or located outside every
extracted clause, are recorded but not sent -- there is nothing to attach a
judgment to.

Where both parties raise an issue on the SAME clause, the clause takes the
UNION: cat1 if either side's issue was cat1, cat2 if either side's was cat2.

Usage:
    python spellbook/risks_negotiation/review.py
    python spellbook/risks_negotiation/review.py --contract 390FSupp3d645_the_agreement
    python spellbook/risks_negotiation/review.py --estimate     # price it, send nothing
    python spellbook/risks_negotiation/review.py --dry-run      # print the prompt
    python spellbook/risks_negotiation/review.py --force        # redo finished ones

Without `--force`, a contract is redone only when one of its pasted `.txt`
files is newer than its result file, so adding a party never costs a re-run of
the others.
"""
import argparse
import csv
import difflib
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "spellbook" / "risks_negotiation" / "output"
RESULTS = ROOT / "spellbook" / "risks_negotiation" / "results"
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

MODEL = "gpt-5.6-sol"
RETRIES = 4

# ============================================================== 1. PARSING ==

# The three severities of the Risks & Negotiation tab, and the five headings the
# Proofread tab groups by. Whichever appears is the issue's first line. Miss one
# and its issues keep the heading as their title and swallow the real title into
# the description, so this set is checked against the pasted reviews rather than
# assumed -- `Section References` only shows up on contracts with numbered
# sections, and appears in 3 of the 20.
SEVERITIES = {"Important", "Notable", "Minor"}
CATEGORIES = {"Definitions", "Spelling + Grammar", "Templated Content",
              "Internal Annotations", "Section References"}

# Where the revision snippet begins. `Capitalize term(s)` and `Add definition`
# are what a Definitions issue shows instead of `Revision`.
REVISION_MARKERS = {"Revision", "Capitalize term(s)", "Add definition"}
COMMENT_MARKER = "Comment"
# Buttons and toggles. `Internal` is furniture; `Internal Annotations` is a
# category, so these are matched whole-line and never as a prefix.
FURNITURE = {"Counterparty", "Internal", "Show", "Dismiss", "Apply"}
COUNTER_RE = re.compile(r"^\d+\s*/\s*\d+$")            # `1 / 14`
APPLY_ALL_RE = re.compile(r"^Apply All \((\d+)\)$")


def split_issues(text):
    """The pasted review -> one list of lines per issue.

    An issue ends at `Apply` or `Apply All (N)`; whatever follows starts the
    next one. Anything after the final `Apply` is trailing UI and dropped.
    """
    issues, cur = [], []
    for raw in text.split("\n"):
        line = raw.strip()
        cur.append(line)
        if line == "Apply" or APPLY_ALL_RE.match(line):
            issues.append(cur)
            cur = []
    return issues


def parse_issue(lines):
    """One issue's lines -> its fields, or None if it holds no title."""
    body = [l for l in lines if l]
    if not body:
        return None

    kind = None
    if body[0] in SEVERITIES or body[0] in CATEGORIES:
        kind = body.pop(0)

    out = {"kind": kind,
           "tab": "risks_negotiation" if kind in SEVERITIES else "proofread",
           "title": None, "description": "", "revision": "", "comment": "",
           "group_size": 1}

    section = "head"
    parts = defaultdict(list)
    for line in body:
        m = APPLY_ALL_RE.match(line)
        if m:
            out["group_size"] = int(m.group(1))
            continue
        if COUNTER_RE.match(line) or line in FURNITURE:
            continue
        if line in REVISION_MARKERS:
            section = "revision"
            continue
        if line == COMMENT_MARKER:
            section = "comment"
            continue
        parts[section].append(line)

    if not parts["head"]:
        return None
    out["title"] = parts["head"][0]
    out["description"] = " ".join(parts["head"][1:]).strip()
    out["revision"] = " ".join(parts["revision"]).strip()
    out["comment"] = " ".join(parts["comment"]).strip()
    return out


# ============================================================= 2. LOCATING ==

NGRAM = 3          # words per index key
WINDOW = 120       # contract words either side of the voted position

# A match is accepted on EITHER test. Coverage alone rejects a revision that is
# mostly Spellbook's new wording -- the severability fix quotes five original
# words and then twenty of its own, covering 0.2 while naming its position
# exactly. A run of MIN_RUN consecutive words is specific enough on its own.
MIN_COVER = 0.40   # share of the snippet's words found, in order
MIN_RUN = 4        # ...or this many consecutive words matched

_WORD = re.compile(r"[A-Za-z0-9']+")


def tokenise(text):
    """[(lowercased word, start, end)] over `text`."""
    return [(m.group().lower(), m.start(), m.end()) for m in _WORD.finditer(text)]


def build_index(tokens):
    """word 3-gram -> the token positions it starts at."""
    idx = defaultdict(list)
    words = [t[0] for t in tokens]
    for i in range(len(words) - NGRAM + 1):
        idx[tuple(words[i:i + NGRAM])].append(i)
    return idx


def locate(snippet, tokens, index):
    """Where in the contract `snippet` sits -> (start, end, cover, run) chars.

    Returns None when nothing matches well enough. The snippet is diff-merged,
    so a good match is partial by construction: how much of the snippet was
    found, and the longest unbroken run, are what decide.
    """
    probe = [w for w, _, _ in tokenise(snippet)]
    if len(probe) < NGRAM:
        return None

    # vote for a region: every shared 3-gram nominates a start position
    votes = Counter()
    for i in range(len(probe) - NGRAM + 1):
        for pos in index.get(tuple(probe[i:i + NGRAM]), ()):
            votes[max(0, pos - i)] += 1
    if not votes:
        return None

    best = None
    words = [t[0] for t in tokens]
    for guess, _n in votes.most_common(5):
        lo = max(0, guess - WINDOW // 2)
        hi = min(len(words), guess + len(probe) + WINDOW // 2)
        sm = difflib.SequenceMatcher(None, probe, words[lo:hi], autojunk=False)
        blocks = [b for b in sm.get_matching_blocks() if b.size]
        if not blocks:
            continue
        # how much of the snippet was found, not how much of the window
        covered = sum(b.size for b in blocks) / len(probe)
        run = max(b.size for b in blocks)
        first, last = blocks[0], blocks[-1]
        s = lo + first.b
        e = lo + last.b + last.size - 1
        cand = (tokens[s][1], tokens[e][2], covered, run)
        if best is None or (covered, run) > (best[2], best[3]):
            best = cand
    if best is None or (best[2] < MIN_COVER and best[3] < MIN_RUN):
        return None
    return best


def clause_index(cid):
    """(start, end, clause_id, label, name) per extracted clause, in order."""
    spans = []
    with open(ROOT / "output" / "dataset.csv", newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["contract_id"] != cid:
                continue
            s0, s1 = (int(x) for x in r["source_span"].split("-"))
            spans.append((s0, s1, r["clause_id"], r["label"], r["clause_name"]))
    return sorted(spans)


def clause_at(spans, start, end):
    """The clause a character span falls in, or the one it overlaps most."""
    best, overlap = None, 0
    for s0, s1, clause_id, label, name in spans:
        n = min(end, s1) - max(start, s0)
        if n > overlap:
            best, overlap = (clause_id, label, name), n
    if best is None:
        return None
    return {"clause_id": best[0], "label": best[1], "clause_name": best[2]}


def parse_party(path, raw, tokens, index, spans):
    """One pasted review -> its issues, each located or explicitly not."""
    issues = []
    for block in split_issues(path.read_text(encoding="utf-8")):
        issue = parse_issue(block)
        if not issue:
            continue
        # The revision is the anchor. Where it is entirely Spellbook's own new
        # wording -- the contractor disclaimer proposes a whole new sentence --
        # the description usually quotes the original instead, so every
        # quotation in it is tried as a fallback probe.
        probes = [issue["revision"]]
        probes += re.findall(r'"([^"\n]{16,})"', issue["description"])
        found = None
        for probe in probes:
            if probe.strip():
                found = locate(probe, tokens, index)
                if found:
                    break
        if found:
            s, e, cover, run = found
            issue["location"] = {
                "start_char": s, "end_char": e,
                "start_line": raw.count("\n", 0, s) + 1,
                "end_line": raw.count("\n", 0, e) + 1,
                "match_cover": round(cover, 3), "match_run": run,
                "matched_text": " ".join(raw[s:e].split())[:300],
                "clause": clause_at(spans, s, e),
            }
        else:
            issue["location"] = None
        issues.append(issue)
    return issues


# ========================================================== 3. CLASSIFYING ==

# Lifted verbatim from `prompts/exp3.md` so this pass and the main experiment
# judge against the same definitions -- otherwise the comparison is between two
# different questions.
RISK_CATEGORIES = """\
**CATEGORY 1 - an intrinsic textual defect, visible in the provision itself.**

- **1.1 Lexical ambiguity or vagueness.** A specific word or phrase genuinely
  carries more than one reasonable meaning, or is so vague its boundary cannot
  be applied. You must be able to *name* the term.
- **1.2 Mechanical error.** A mistake in writing, grammar, spelling or
  punctuation that changes what the provision means.
- **1.3 General-vs-specific / list scope.** A general catch-all sits against
  enumerated specifics, leaving the catch-all's reach uncertain (ejusdem
  generis, expressio unius); or the provision is so one-sidedly drafted that a
  genuine ambiguity would be construed against its drafter.

**CATEGORY 2 - the defect arises from the provision's RELATIONSHIP to the rest
of the instrument.** You must consult the other provisions of the contract.

- **2.1 Conflicting clauses.** This provision directly contradicts another
  operative provision of the same contract.
- **2.2 Whole-instrument incoherence.** The provision cannot be reconciled with
  the contract read as a whole; harmonising every provision still leaves a
  genuine internal inconsistency.
- **2.3 Recitals vs operative text.** A recital and an operative term point in
  different directions.
"""

SYSTEM = f"""\
You are a precise contract-construction analyst.

You are given the full text of one contract and a list of issues that a
contract-review tool raised against it. The tool reviews for NEGOTIATION risk on
behalf of a party. Your job is different: decide, for each issue, whether the
defect it describes is one of two kinds of CONSTRUCTION risk -- something a court
would have to construe -- or neither.

### The two categories

{RISK_CATEGORIES}

### What to decide

For each issue, ask whether what it raises is related to Category 1 or Category
2. If it is related at all -- even a little, even where a court might well go
the other way -- assign that category, and use `reason` to say what the
connection is. If it is related to neither, return `none` and say why not.

You judge how much relation is enough; there is no rubric to apply beyond the
definitions above. Where an issue is related to both categories, choose the
closer one and say so in `reason`.
"""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["judgments"],
    "properties": {
        "judgments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["ref", "category", "subcategory", "reason"],
                "properties": {
                    "ref": {"type": "integer",
                            "description": "the issue's [n] number"},
                    "category": {"type": "string",
                                 "enum": ["cat1", "cat2", "none"]},
                    "subcategory": {
                        "type": "string",
                        "enum": ["1.1", "1.2", "1.3", "2.1", "2.2", "2.3",
                                 "none"]},
                    "reason": {
                        "type": "string",
                        "description": "one or two sentences; for cat2, name "
                                       "the conflicting provision"},
                },
            },
        }
    },
}


def judgeable(parties):
    """[(party, index, issue)] worth sending, and why the rest were not.

    An issue with no location, or one located in a stretch of the document that
    was never extracted as a clause, has nothing to attach a judgment to.
    """
    keep = []
    for party, issues in parties.items():
        for i, issue in enumerate(issues):
            loc = issue["location"]
            if not loc:
                issue["skipped"] = "no match in the contract"
            elif not loc["clause"]:
                issue["skipped"] = "matched outside every clause"
            else:
                issue["skipped"] = None
                keep.append((party, i, issue))
    return keep


def issue_block(items):
    """The numbered issue list sent up. `ref` is the index into `items`."""
    out = []
    for ref, (party, _, issue) in enumerate(items):
        clause = issue["location"]["clause"]
        lines = [f"[{ref}] raised for: {party}",
                 f"    title: {issue['title']}"]
        if issue["description"]:
            lines.append(f"    the tool's reasoning: {issue['description']}")
        if issue["comment"]:
            lines.append(f"    the tool's comment on its edit: {issue['comment']}")
        lines.append(f"    clause it lands in: {clause['clause_id']} "
                     f"({clause['clause_name']})")
        lines.append(f"    text matched: {issue['location']['matched_text']}")
        out.append("\n".join(lines))
    return "\n\n".join(out)


def user_prompt(cid, document, items):
    return f"""\
### The contract

- **contract_id:** {cid}

---------- CONTRACT {cid} START ----------
{document}
---------- CONTRACT {cid} END ----------

### The issues to judge

Each issue below was raised by the review tool on behalf of one of the two
parties, and has been matched to the clause of the contract it falls in.

{issue_block(items)}

### Your task

Return one judgment for every issue above, {len(items)} in total, keyed by its
`ref` number. Judge each issue independently.
"""


def call(client, cid, document, items):
    prompt = user_prompt(cid, document, items)
    last = None
    for attempt in range(RETRIES):
        try:
            r = client.responses.create(
                model=MODEL,
                input=[{"role": "system", "content": SYSTEM},
                       {"role": "user", "content": prompt}],
                text={"format": {"type": "json_schema", "name": "judgments",
                                 "strict": True, "schema": SCHEMA}},
            )
            return json.loads(r.output_text), r.usage
        except Exception as e:                       # transient 429/5xx/timeouts
            last = e
            if attempt == RETRIES - 1:
                break
            wait = 4 * 2 ** attempt
            print(f"    {type(e).__name__}: {str(e)[:120]} -- retry in {wait}s")
            time.sleep(wait)
    raise last


def rollup(items):
    """Per-clause verdict, taking the UNION over both parties' issues.

    A clause both sides raised something on is flagged cat1 if EITHER side's
    issue was cat1, and cat2 if either side's was cat2 -- the two reviews are
    two looks at the same text, not two votes to be averaged.
    """
    clauses = {}
    for party, _i, issue in items:
        j = issue.get("judgment")
        if not j:
            continue
        c = issue["location"]["clause"]
        row = clauses.setdefault(c["clause_id"], {
            "clause_id": c["clause_id"], "clause_name": c["clause_name"],
            "label": c["label"], "cat1": False, "cat2": False,
            "parties": [], "n_issues": 0, "judgments": []})
        row["n_issues"] += 1
        if party not in row["parties"]:
            row["parties"].append(party)
        if j["category"] == "cat1":
            row["cat1"] = True
        elif j["category"] == "cat2":
            row["cat2"] = True
        row["judgments"].append(
            {"party": party, "title": issue["title"],
             "category": j["category"], "subcategory": j["subcategory"],
             "reason": j["reason"]})
    for row in clauses.values():
        row["risky"] = row["cat1"] or row["cat2"]
    return dict(sorted(clauses.items()))


# ===================================================================== run ==
def read_contract(cid):
    return (ROOT / "output" / "contracts" / f"{cid}.md").read_text(
        encoding="utf-8")


def parse_contract(cid, folder):
    """Every party's review, parsed and located. Returns (raw, spans, parties)."""
    raw = read_contract(cid)
    tokens = tokenise(raw)
    index = build_index(tokens)
    spans = clause_index(cid)
    parties = {}
    for path in sorted(folder.glob("*.txt")):
        if path.stat().st_size <= 2:
            continue
        parties[path.stem] = parse_party(path, raw, tokens, index, spans)
    return raw, spans, parties


def review(client, cid, folder, dry_run=False):
    raw, spans, parties = parse_contract(cid, folder)
    items = judgeable(parties)
    n_issues = sum(len(v) for v in parties.values())
    n_located = sum(1 for v in parties.values() for i in v if i["location"])

    print(cid)
    for party, issues in parties.items():
        loc = sum(1 for i in issues if i["location"])
        cl = sum(1 for i in issues if i["location"] and i["location"]["clause"])
        print(f"  {party:32} {len(issues):3} issues, {loc:3} located, "
              f"{cl:3} inside a clause")
    if not items:
        print("  nothing to judge")
        return None
    if dry_run:
        print(user_prompt(cid, "<contract omitted>", items))
        return None

    result, usage = call(client, cid, raw, items)
    by_ref = {j["ref"]: j for j in result["judgments"]}
    missing = [r for r in range(len(items)) if r not in by_ref]
    if missing:
        print(f"  !! no judgment returned for ref {missing}")
    for ref, (_party, _i, issue) in enumerate(items):
        j = by_ref.get(ref)
        issue["judgment"] = None if j is None else {
            "category": j["category"], "subcategory": j["subcategory"],
            "reason": j["reason"]}

    judged = [i for _p, _i, i in items if i.get("judgment")]
    counts = {k: sum(1 for i in judged if i["judgment"]["category"] == k)
              for k in ("cat1", "cat2", "none")}
    clauses = rollup(items)

    out = {
        "contract_id": cid,
        "model": MODEL,
        "n_clauses": len(spans),
        "n_issues": n_issues,
        "n_located": n_located,
        "n_judged": len(items),
        "n_skipped": n_issues - len(items),
        "counts": counts,
        "parties": {
            party: {
                "n_issues": len(issues),
                "n_located": sum(1 for i in issues if i["location"]),
                "by_tab": dict(Counter(i["tab"] for i in issues)),
                "by_kind": dict(Counter(i["kind"] for i in issues)),
                "issues": issues,
            } for party, issues in parties.items()},
        "clauses": clauses,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{cid}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    flagged = [c for c in clauses.values() if c["risky"]]
    hits = [c for c in flagged if c["label"] == "POSITIVE"]
    print(f"  {len(items)} judged, {out['n_skipped']} skipped  ->  "
          f"cat1 {counts['cat1']}, cat2 {counts['cat2']}, none {counts['none']}")
    print(f"  {len(flagged)} of {len(clauses)} touched clause(s) flagged risky; "
          f"{len(hits)} gold POSITIVE "
          f"({', '.join(c['clause_id'] for c in hits) or '-'})")
    print(f"  tokens in {usage.input_tokens}, out {usage.output_tokens}")
    return out


# ----------------------------------------------------------------- pricing --
# gpt-5.6-sol's published rates, $ per million tokens. The API does not report
# them, so they are set here and shown in the estimate -- change them here if
# they move rather than reading the printed total as authoritative.
PRICE_IN = 1.25
PRICE_OUT = 10.00


def est(todo):
    """What the outstanding calls would cost, before any is sent.

    The input side is exact: every prompt is BUILT as `review` would build it
    and metered with tiktoken. The output side cannot be known in advance --
    this is a reasoning model and reasoning tokens are billed as output -- so
    three rates per judgment are shown rather than a forecast.
    """
    import tiktoken
    enc = tiktoken.get_encoding("o200k_base")
    sys_tok = len(enc.encode(SYSTEM))

    rows, n_issues = [], 0
    for cid, folder in todo:
        _raw, _spans, parties = parse_contract(cid, folder)
        items = judgeable(parties)
        if not items:
            continue
        tok = sys_tok + len(enc.encode(
            user_prompt(cid, read_contract(cid), items)))
        rows.append((cid, tok, len(items)))
        n_issues += len(items)

    if not rows:
        print("nothing outstanding -- no calls to price")
        return

    tin = sum(r[1] for r in rows)
    big = max(rows, key=lambda r: r[1])
    most = max(rows, key=lambda r: r[2])
    print(f"{len(rows)} call(s), one per contract, {n_issues:,} issue(s) "
          f"to judge")
    print(f"  largest input: {big[1]:,} tokens in {big[0]}")
    print(f"  most issues in one call: {most[2]} in {most[0]}")
    print(f"\n  input  {tin / 1e6:.3f}M tokens  @ ${PRICE_IN}/M  "
          f"=  ${tin / 1e6 * PRICE_IN:6.2f}")
    for per in (90, 200, 400):
        tout = n_issues * per
        print(f"  output {tout / 1e6:.3f}M ({per:3d} tok/judgment, reasoning "
              f"included) @ ${PRICE_OUT}/M = ${tout / 1e6 * PRICE_OUT:6.2f}"
              f"    TOTAL ${(tin * PRICE_IN + tout * PRICE_OUT) / 1e6:6.2f}")


def outstanding(force, only):
    """The contracts with something pasted that still need a run."""
    todo = []
    for folder in sorted(d for d in OUT.iterdir() if d.is_dir()):
        if only and folder.name != only:
            continue
        pasted = [p for p in folder.glob("*.txt") if p.stat().st_size > 2]
        if not pasted:
            continue
        done = RESULTS / f"{folder.name}.json"
        # A newly pasted party makes the stored result stale; nothing else does,
        # so adding one side never costs a re-run of the other contracts.
        if not force and done.exists() and \
                done.stat().st_mtime >= max(p.stat().st_mtime for p in pasted):
            continue
        todo.append((folder.name, folder))
    return todo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract", help="one contract_id only")
    ap.add_argument("--force", action="store_true",
                    help="redo contracts whose result is already up to date")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the prompt instead of calling the API")
    ap.add_argument("--estimate", action="store_true",
                    help="price the outstanding calls, send nothing")
    args = ap.parse_args()

    # The contracts are OCR'd and carry (c), curly quotes and the odd accent;
    # a Windows console defaults to cp936/cp1252 and --dry-run dies on them.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if args.contract and not (OUT / args.contract).is_dir():
        sys.exit(f"{args.contract}: no such directory under {OUT}")
    todo = outstanding(args.force or args.dry_run or args.estimate,
                       args.contract)
    if not todo:
        print("nothing to do (use --force to redo)")
        return
    if args.estimate:
        est(todo)
        return

    load_dotenv(ROOT / ".env")
    client = None if args.dry_run else OpenAI()

    totals = defaultdict(int)
    for cid, folder in todo:
        out = review(client, cid, folder, args.dry_run)
        if out:
            for k, v in out["counts"].items():
                totals[k] += v
            totals["judged"] += out["n_judged"]
            totals["skipped"] += out["n_skipped"]

    if totals:
        print(f"\n{len(todo)} contract(s) written to "
              f"{RESULTS.relative_to(ROOT)}/<contract_id>.json")
        print(f"{totals['judged']} issue(s) judged, {totals['skipped']} "
              f"skipped -- cat1 {totals['cat1']}, cat2 {totals['cat2']}, "
              f"none {totals['none']}")


if __name__ == "__main__":
    main()

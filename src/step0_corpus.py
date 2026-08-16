"""Step 0 — build the corpus (no LLM).

Two jobs, neither of which needs a model:

1. **Link.** Join the Westlaw headnotes, the opinion text and the docket linking
   sheet, and keep the cases worth spending money on. A case is in scope when it
   is filed under one of the twelve taxonomy keys, its opinion text is
   available, and the linking sheet shows at least one Bloomberg entry document
   — i.e. the contract can actually be downloaded. Doing the acquisition match
   first is what makes it affordable to give the model whole opinions later
   instead of regex-harvested snippets.

2. **Register.** Write each contract the Contract-Risk repo extracted to
   output/contracts/<cid>.md, stripped, and record it in output/contracts.json.

The contract text is not OCR'd here and no longer comes from Datalab. It comes
from the Contract-Risk repo, which for each case already:

  * downloaded the docket filings and OCR'd them (`ocrmypdf --force-ocr`),
  * had a model say which file holds which named agreement, and
  * sliced that file down to the contract's own lines — a VERBATIM line-range
    cut, made by the same reasoning this pipeline uses: the model returns line
    numbers and the script cuts the lines, so no model wrote the words.
  * checked each result against the opinion: is this the contract the court was
    construing, and is it readable? That verdict is `contract_check.csv`.

So step 1 is handed contracts, not whole bundles of filings. That is cheaper and
better targeted than the old arrangement, but it moves one judgement upstream:
WHICH document is the contract is now their answer rather than step 1's. Their
verdict is carried onto every row of contracts.json, so a doubtful one can be
found again. --verdict decides which are registered.

Input : data/wl-headnotes-parsed/<key>/citations.csv
        data/opinions-case-dot-law.csv
        data/Agreements Docket-Opinion Linking Data.xlsx
        contract_risk/generated/corpus/contracts/contract_extraction.csv
        contract_risk/generated/corpus/contracts/contract_check.csv
        contract_risk/generated/corpus/contracts/extracted_contracts/*.txt
Output: output/cases.json, output/opinions/<id>.txt
        output/contracts.json, output/contracts/<cid>.md

Usage:
    python src/step0_corpus.py [--case CITATION] [--verdict usable,partial]
"""
import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path

import openpyxl

import lib

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

DUP = 0.90        # two documents this alike are one contract, filed twice
SHINGLE = 8       # words per fingerprint shingle
VERDICTS = "usable"   # which contract_check verdicts are registered by default


def norm(citation):
    return re.sub(r"[^a-z0-9]", "", str(citation or "").lower())


# ------------------------------------------------------------------ link ---
def headnote_cases():
    """normalised citation -> {key label: [headnotes]}, plus the display form."""
    keys, display = {}, {}
    for folder, (label, _code, _about) in lib.KEYS.items():
        path = lib.DATA / "wl-headnotes-parsed" / folder / "citations.csv"
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                cit = " ".join(row["citation"].split())
                if not cit:
                    continue
                n = norm(cit)
                display.setdefault(n, cit)
                keys.setdefault(n, {}).setdefault(label, []).append(
                    " ".join(row.get("headnote", "").split()))
    return keys, display


def entry_documents():
    """normalised citation -> number of distinct downloadable entry documents."""
    wb = openpyxl.load_workbook(
        lib.DATA / "Agreements Docket-Opinion Linking Data.xlsx",
        read_only=True, data_only=True)
    rows = wb.active.iter_rows(values_only=True)
    idx = {h: i for i, h in enumerate(next(rows))}
    docs = {}
    for r in rows:
        cit, doc = r[idx["Case Citation"]], r[idx["Entry Document"]]
        if cit and doc:
            docs.setdefault(norm(cit), set()).add(str(doc).strip())
    return {k: len(v) for k, v in docs.items()}


def extracted():
    """normalised citation -> the contracts Contract-Risk extracted for it.

    Joins their two records on (case, agreement): `contract_extraction.csv` says
    where each slice came from, `contract_check.csv` says whether it is the right
    contract and readable. Both are carried through, because a row of this
    dataset should be traceable to the file and line range it was cut from
    without opening their repo.
    """
    out = {}
    if not lib.EXTRACTION.is_file():
        return out

    verdicts = {}
    if lib.CHECK.is_file():
        with open(lib.CHECK, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                verdicts[(r["case_citation"], r["agreement"])] = r

    with open(lib.EXTRACTION, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            v = verdicts.get((r["case_citation"], r["agreement"]), {})
            out.setdefault(norm(r["case_citation"]), []).append({
                "agreement": r["agreement"],
                "file": lib.EXTRACTED / Path(r["extracted_file"]).name,
                "source": r["source_file"],
                "spans": r["spans"],
                "extract_confidence": r["confidence"],
                "extract_note": r["note"],
                "verdict": v.get("verdict", "unchecked"),
                "check_reason": v.get("reason", ""),
            })
    return out


def link():
    """The cases worth acquiring, and their opinion text on disk."""
    keys, display = headnote_cases()
    docs = entry_documents()
    have = extracted()
    wanted = set(keys) & set(docs)
    print(f"{len(keys)} cases under the 12 keys | {len(docs)} with entry "
          f"documents | {len(wanted)} both")

    lib.OPINIONS.mkdir(parents=True, exist_ok=True)
    cases = {}
    with open(lib.DATA / "opinions-case-dot-law.csv", newline="",
              encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            n = norm(row["citation"])
            if n not in wanted or n in cases:
                continue
            citation = display[n]
            cid = lib.cite_id(citation)
            (lib.OPINIONS / f"{cid}.txt").write_text(row["text"], encoding="utf-8")
            cases[citation] = {
                "id": cid,
                "case_name": row.get("case_name", "").strip(),
                "keys": keys[n],
                "taxonomy": sorted({lib.KEY_BY_LABEL[k][1] for k in keys[n]}),
                "entry_documents": docs[n],
                # Their agreement names and verdicts, not paths: what is on offer
                # for this case, before --verdict decides what is registered.
                "contracts": [{"agreement": c["agreement"], "verdict": c["verdict"]}
                              for c in have.get(n, [])],
            }

    lib.write_json(lib.OUT / "cases.json", cases)
    with_text = [c for c in cases.values() if c["contracts"]]
    offered = Counter(x["verdict"] for c in with_text for x in c["contracts"])
    print(f"{len(cases)} cases have opinion text; {len(with_text)} have an "
          f"extracted contract ({sum(offered.values())} contracts: "
          f"{', '.join(f'{n} {v}' for v, n in offered.most_common())})")
    # Contract-Risk covers a sample, so most in-scope cases have no contract text
    # yet. That is a gap in acquisition, not an error here.
    print(f"  -> {lib.OPINIONS.relative_to(lib.ROOT)}/<id>.txt")
    return cases, have


# -------------------------------------------------------------- register ---
def contract_id(citation, agreement, taken):
    """The id is built from the citation and the agreement's name.

    Their agreement name, not one a model of ours invented, and it is stable:
    they wrote it once and it is what their two CSVs are keyed on, so the same
    input always produces the same id. `taken` appends a numeric suffix if a base
    id ever repeats — two agreements of one case can slug to the same four words
    — so one registration can never silently overwrite another.
    """
    base = f"{lib.cite_id(citation)}_{lib.slug(agreement)}"
    cid, n = base, 1
    while cid in taken:
        n += 1
        cid = f"{base}_{n}"
    return cid


def shingles(text, k=SHINGLE):
    """The set of overlapping k-word runs in `text`.

    A document's fingerprint. Building it is linear, and comparing two is linear
    in the smaller — which is the whole point: `difflib.SequenceMatcher.ratio`
    is quadratic with `autojunk=False`, and these documents run to 690,000
    characters. It ran for eleven minutes on one pair of agreements before this
    replaced it. The cheap `real_quick_ratio`/`quick_ratio` gates that used to
    guard it do not help, because those compare bags of characters and two
    unrelated English contracts have nearly identical ones.
    """
    words = text.split()
    return {" ".join(words[i:i + k]) for i in range(max(1, len(words) - k + 1))}


def same(a, b):
    """Is this the same document, extracted twice under two agreement names?

    Containment, not Jaccard: what is being caught is one document filed twice,
    and the second copy often carries extra pages — a cover sheet, a further
    schedule. Measuring against the SMALLER fingerprint keeps that a duplicate,
    where Jaccard would call it a new document because of the extra.
    """
    sa, sb = shingles(a), shingles(b)
    if not sa or not sb:
        return False
    return len(sa & sb) / min(len(sa), len(sb)) >= DUP


def candidates(offered, verdicts):
    """One case's extracted contracts, stripped and worth a call.

    Two filters, both cheap and both printed. The verdict is Contract-Risk's own
    check of whether this is the contract the court construed and whether it can
    be read; registering an `empty_or_unreadable` one would spend a step-2 call
    to locate clauses in noise. The character floor is the one lib.n_tokens
    already uses: no token is shorter than one character, so a file under
    MIN_INPUT_TOKENS characters cannot reach the token floor either.

    `strip_pdf_text`, not `strip_ocr`: their files are OCR'd plain text, not the
    Datalab markdown the latter was written for.
    """
    out = []
    for c in offered:
        if c["verdict"] not in verdicts:
            print(f"  skip {c['agreement'][:52]}: verdict {c['verdict']}")
            continue
        if not c["file"].is_file():
            print(f"  skip {c['agreement'][:52]}: {c['file'].name} is not on disk")
            continue
        text = lib.strip_pdf_text(c["file"].read_text(encoding="utf-8",
                                                     errors="replace"))
        if len(text) < lib.MIN_INPUT_TOKENS:
            print(f"  skip {c['agreement'][:52]}: {len(text)} characters")
            continue
        out.append({**c, "text": text,
                    "norm": " ".join(text.split()).lower()})
    return out


def register(cases, have, only, verdicts):
    """output/contracts/<cid>.md and the registry, for every case in scope."""
    # A full rebuild starts from an empty registry, so it reproduces the same
    # ids every time. Carrying the previous one over would make every id collide
    # with itself and pick up a `_2` suffix — this step is not resumable and
    # does not need to be, since it makes no model calls. `--case` is the
    # exception: it keeps the other citations so one case can be rebuilt alone.
    registry = ({k: v for k, v in
                 lib.read_json(lib.OUT / "contracts.json", {}).items()
                 if v["citation"] != only} if only else {})
    lib.CONTRACTS.mkdir(parents=True, exist_ok=True)

    for citation, case in sorted(cases.items()):
        offered = have.get(norm(citation), [])
        if (only and citation != only) or not offered:
            continue
        print(f"{citation}  ({len(offered)} extracted)")
        found = candidates(offered, verdicts)
        # The last key is a tiebreak, so which copy of a duplicated document
        # wins — and therefore what everything downstream is keyed on — does not
        # depend on the order of their CSV.
        found.sort(key=lambda c: (-len(c["text"]), c["agreement"]))

        keep = []
        for c in found:
            # They key on (case, agreement), so the same file cited under two
            # names in the opinion is extracted twice. Two contract_ids over one
            # document would put the same clause in the dataset twice, which
            # build_dataset.py asserts against — better caught here, by name.
            dup = next((k for k in keep if same(c["norm"], k["norm"])), None)
            if dup:
                print(f"  duplicate: {c['agreement'][:44]!r} is the same "
                      f"document as {dup['agreement'][:44]!r}")
                continue
            keep.append(c)

        for c in sorted(keep, key=lambda c: c["agreement"]):
            cid = contract_id(citation, c["agreement"], registry)
            path = lib.CONTRACTS / f"{cid}.md"
            path.write_text(c["text"], encoding="utf-8")
            registry[cid] = {
                "citation": citation,
                "file": str(path.relative_to(lib.ROOT)).replace("\\", "/"),
                "chars": len(c["text"]),
                "lines": c["text"].count("\n") + 1,
                # Where this text came from, in their repo. Enough to re-cut it
                # from the OCR without opening this one.
                "agreement": c["agreement"],
                "source": c["source"],
                "source_spans": c["spans"],
                "extract_confidence": c["extract_confidence"],
                "verdict": c["verdict"],
                "extract_note": c["extract_note"],
            }
            print(f"  {cid}: {len(c['text']):,} chars "
                  f"[{c['verdict']}/{c['extract_confidence']}]")

    lib.write_json(lib.OUT / "contracts.json", registry)
    by_case = len({v["citation"] for v in registry.values()})
    print(f"{len(registry)} contracts registered over {by_case} cases")
    return registry


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", help="register one citation's contracts only")
    ap.add_argument("--verdict", default=VERDICTS,
                    help=f"comma-separated contract_check verdicts to register "
                         f"(default {VERDICTS}; 'all' for every one)")
    ap.add_argument("--skip-link", action="store_true",
                    help="reuse output/cases.json instead of rebuilding it")
    args = ap.parse_args()

    if not lib.EXTRACTION.is_file():
        raise SystemExit(
            f"{lib.EXTRACTION.relative_to(lib.ROOT)} is not there — the contract "
            f"text comes from the Contract-Risk repo, which has to be reachable "
            f"at {lib.SOURCE.parents[2].relative_to(lib.ROOT)}/ (see README)")

    if args.skip_link:
        cases = lib.read_json(lib.OUT / "cases.json", {})
        if not cases:
            raise SystemExit("--skip-link needs an existing output/cases.json")
        have = extracted()
    else:
        cases, have = link()

    verdicts = ({v["verdict"] for c in have.values() for v in c}
                if args.verdict == "all"
                else {v.strip() for v in args.verdict.split(",")})
    register(cases, have, args.case, verdicts)


if __name__ == "__main__":
    main()

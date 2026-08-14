"""Step 0 — build the corpus (no LLM).

Two jobs, neither of which needs a model:

1. **Link.** Join the Westlaw headnotes, the opinion text and the docket linking
   sheet, and keep the cases worth spending money on. A case is in scope when it
   is filed under one of the twelve taxonomy keys, its opinion text is
   available, and the linking sheet shows at least one Bloomberg entry document
   — i.e. the contract can actually be downloaded. Doing the acquisition match
   first is what makes it affordable to give the model whole opinions later
   instead of regex-harvested snippets.

2. **Register.** Write every OCR'd file of those cases' bundles to
   output/contracts/<cid>.md, stripped, and record it in output/contracts.json.

Which of the registered documents is a *contract* is deliberately not decided
here, because deciding it needs a model. Step 1 is handed all of them and cites
the ones the court construed; step 2 — the expensive step — runs only on the
ones that won. See docs/DESIGN.md §5.

Input : data/wl-headnotes-parsed/<key>/citations.csv
        data/opinions-case-dot-law.csv
        data/Agreements Docket-Opinion Linking Data.xlsx
        bloomberg_datalab/<bundle>/*.md
Output: output/cases.json, output/opinions/<id>.txt
        output/contracts.json, output/contracts/<cid>.md

Usage:
    python src/step0_corpus.py [--case CITATION]
"""
import argparse
import csv
import difflib
import re
import sys
from pathlib import Path

import openpyxl

import lib

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

DUP = 0.90        # two documents this alike are one contract, filed twice


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


def bundles():
    """normalised citation -> the bundle directories downloaded for it."""
    out = {}
    if not lib.DATALAB.is_dir():
        return out
    for d in sorted(p for p in lib.DATALAB.iterdir() if p.is_dir()):
        out.setdefault(norm(d.name.rsplit("_", 1)[0]), []).append(d.name)
    return out


def link():
    """The cases worth acquiring, and their opinion text on disk."""
    keys, display = headnote_cases()
    docs = entry_documents()
    downloaded = bundles()
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
                "bundles": downloaded.get(n, []),
            }

    lib.write_json(lib.OUT / "cases.json", cases)
    with_bundle = sum(1 for c in cases.values() if c["bundles"])
    print(f"{len(cases)} cases have opinion text; {with_bundle} have a bundle "
          f"downloaded ({sum(len(c['bundles']) for c in cases.values())} bundles)")
    print(f"  -> {lib.OPINIONS.relative_to(lib.ROOT)}/<id>.txt")
    return cases


# -------------------------------------------------------------- register ---
def contract_id(citation, source, taken):
    """The id is built from the citation and the file the document came from.

    Never from a name a model gave it: the model words that name differently on
    every run, and two documents in one case can slug to the same four words.
    `taken` appends a numeric suffix if a base id ever repeats, so one
    registration can never silently overwrite another.
    """
    base = f"{lib.cite_id(citation)}_{lib.slug(Path(source).stem)}"
    cid, n = base, 1
    while cid in taken:
        n += 1
        cid = f"{base}_{n}"
    return cid


def same(a, b):
    """Is this the same document, attached to a second filing?"""
    m = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return (m.real_quick_ratio() >= DUP and m.quick_ratio() >= DUP
            and m.ratio() >= DUP)


def candidates(case):
    """Every OCR'd file of one case's bundles, stripped and worth a call.

    The floor is the one lib.n_tokens already uses: no token is shorter than one
    character, so a file under MIN_INPUT_TOKENS characters cannot reach the
    token floor either. It costs no API key to apply, and it drops the cover
    sheets, stamps and one-line certificates that make up much of a bundle.
    """
    out = []
    for bundle in case["bundles"]:
        for path in sorted((lib.DATALAB / bundle).glob("*.md")):
            rel = f"{bundle}/{path.name}"
            text = lib.strip_ocr(path.read_text(encoding="utf-8"))
            if len(text) < lib.MIN_INPUT_TOKENS:
                print(f"  skip {rel}: {len(text)} characters")
                continue
            out.append({"source": rel, "text": text,
                        "norm": " ".join(text.split()).lower()})
    return out


def register(cases, only):
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
        if (only and citation != only) or not case["bundles"]:
            continue
        print(f"{citation}  ({len(case['bundles'])} bundle(s))")
        found = candidates(case)
        # The last key is a tiebreak, so which copy of a duplicated document
        # wins — and therefore what everything downstream is keyed on — does not
        # depend on directory order.
        found.sort(key=lambda c: (-len(c["text"]), c["source"]))

        keep = []
        for c in found:
            dup = next((k for k in keep if same(c["norm"], k["norm"])), None)
            if dup:
                print(f"  duplicate: {c['source']} is already registered as "
                      f"{dup['source']}")
                continue
            keep.append(c)

        for c in sorted(keep, key=lambda c: c["source"]):
            cid = contract_id(citation, c["source"], registry)
            path = lib.CONTRACTS / f"{cid}.md"
            path.write_text(c["text"], encoding="utf-8")
            registry[cid] = {
                "citation": citation,
                "file": str(path.relative_to(lib.ROOT)).replace("\\", "/"),
                "source": c["source"],
                "chars": len(c["text"]),
                "lines": c["text"].count("\n") + 1,
            }
            print(f"  {cid}: {len(c['text']):,} chars")

    lib.write_json(lib.OUT / "contracts.json", registry)
    print(f"{len(registry)} documents registered")
    return registry


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", help="register one citation's bundles only")
    ap.add_argument("--skip-link", action="store_true",
                    help="reuse output/cases.json instead of rebuilding it")
    args = ap.parse_args()

    if args.skip_link:
        cases = lib.read_json(lib.OUT / "cases.json", {})
        if not cases:
            raise SystemExit("--skip-link needs an existing output/cases.json")
    else:
        cases = link()
    register(cases, args.case)


if __name__ == "__main__":
    main()

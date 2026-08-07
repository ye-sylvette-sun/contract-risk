"""Step 0 — join headnotes, opinion text and downloadable-contract links.

A case is in scope when it is filed under one of the twelve taxonomy keys, its
opinion text is available, and the linking spreadsheet shows at least one
Bloomberg entry document — i.e. the contract can actually be downloaded. Doing
the acquisition match first is what makes it affordable to give the model whole
opinions later instead of regex-harvested snippets.

Input : data/wl-headnotes-parsed/<key>/citations.csv
        data/opinions-case-dot-law.csv
        data/Agreements Docket-Opinion Linking Data.xlsx
Output: output/cases.json          one entry per case worth acquiring
        output/opinions/<id>.txt   the opinion text every later step reads

Usage:
    conda run -n legal-llm python src/link_cases.py
"""
import csv
import re
import sys

import openpyxl

import lib

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


def norm(citation):
    return re.sub(r"[^a-z0-9]", "", str(citation or "").lower())


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
    for d in sorted(p for p in lib.DATALAB.iterdir() if p.is_dir()):
        out.setdefault(norm(d.name.rsplit("_", 1)[0]), []).append(d.name)
    return out


def main():
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
    with_text = len(cases)
    with_bundle = sum(1 for c in cases.values() if c["bundles"])
    print(f"{with_text} cases have opinion text; {with_bundle} have a bundle "
          f"downloaded ({sum(len(c['bundles']) for c in cases.values())} bundles)")
    print(f"  -> {lib.OPINIONS.relative_to(lib.ROOT)}/<id>.txt")


if __name__ == "__main__":
    main()

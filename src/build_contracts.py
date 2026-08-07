"""Cut the surveyed instruments out of their files (no LLM).

Three jobs: stitch an instrument that runs across more than one file back
together, cut each instrument into output/contracts/<id>.md with every line
number rebased onto that file, and keep one copy of each instrument per
citation.

Input : output/survey.json, bloomberg_datalab/<bundle>/*.md
Output: output/contracts/<id>.md, output/contracts.json

Usage:
    conda run -n legal-llm python src/build_contracts.py [--case CITATION]
"""
import argparse
import difflib
from pathlib import Path

import lib

DUP = 0.90        # two slices this alike are the same instrument attached twice


def contract_id(citation, sources, taken):
    """The id is built from the citation and the file the instrument was cut
    from — never from the instrument's name.

    The model words that name differently on every run (one build called a
    policy `commercial_general_liability_coverage`, the next
    `hartford_fire_insurance_company`), so name-derived ids move for no reason.
    Worse, two instruments in one case can slug to the same four words: 610
    F.Supp.2d 1129 produced `Agreement for Professional Services, Contract
    Employee` twice, and the second registration silently overwrote the first —
    losing a stitched instrument and its amendment. `taken` makes that
    impossible.
    """
    base = f"{lib.cite_id(citation)}_{lib.slug(Path(sources[0]['file']).stem)}"
    cid, n = base, 1
    while cid in taken:
        n += 1
        cid = f"{base}_{n}"
    return cid


def chains(surveys):
    """Group surveyed files into instruments, following continues_from.

    A bundle routinely splits one document across several files, cut mid-clause
    at the boundary. The model names the file each part continues; ordering by
    filename would not work ("144 pages.md" sorts before "Declaration Part 2").
    """
    by_bundle = {}
    for rel, s in surveys.items():
        if s["lines"]:
            by_bundle.setdefault(s["bundle"], {})[s["file"]] = (rel, s)

    out = []
    for bundle, files in by_bundle.items():
        nxt = {}                                    # file -> the file after it
        for name, (_rel, s) in files.items():
            prev = s["continues_from"]
            if prev and prev in files and prev != name and prev not in nxt:
                nxt[prev] = name
        heads = [n for n in files if n not in nxt.values()]
        for head in sorted(heads):
            chain, name = [], head
            while name and name in files and files[name][0] not in [c[0] for c in chain]:
                chain.append(files[name])
                name = nxt.get(name)
            out.append(chain)
    return out


def assemble(chain):
    """One instrument's text, with every provision's lines rebased onto it."""
    body, provisions, sources, offset = [], [], [], 0
    for rel, s in chain:
        text = lib.strip_ocr((lib.DATALAB / rel).read_text(encoding="utf-8"))
        lo, hi = s["lines"]
        part = text.split("\n")[lo - 1:hi]
        for p in s["provisions"]:
            provisions.append({**p, "lines": [p["lines"][0] - lo + 1 + offset,
                                              p["lines"][1] - lo + 1 + offset]})
        sources.append({"file": rel, "lines": [lo, hi]})
        body += part
        offset += len(part)
    return "\n".join(body), provisions, sources


def norm(text):
    return " ".join(text.split()).lower()


def same(a, b):
    """Is this the same instrument, attached to a second filing?"""
    m = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return (m.real_quick_ratio() >= DUP and m.quick_ratio() >= DUP
            and m.ratio() >= DUP)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", help="build one citation only")
    args = ap.parse_args()

    surveys = lib.read_json(lib.OUT / "survey.json", {})
    if args.case:
        surveys = {k: v for k, v in surveys.items() if v["citation"] == args.case}

    found = {}
    for chain in chains(surveys):
        text, provisions, sources = assemble(chain)
        _rel, first = chain[0]
        found.setdefault(first["citation"], []).append({
            "instrument": first["instrument_name"],
            "note": first["note"], "text": text,
            "provisions": provisions, "sources": sources,
            "norm": norm(text),
        })
        if len(chain) > 1:
            print(f"stitched {len(chain)} files -> {first['instrument_name']}: "
                  f"{', '.join(s['file'].split('/')[-1] for s in sources)}")

    # A full rebuild starts from an empty registry, so it reproduces the same
    # ids every time. Carrying the previous one over would make every id collide
    # with itself and pick up a `_2` suffix — this step is not resumable and does
    # not need to be, since it makes no model calls. `--case` is the exception:
    # it keeps the other citations so one case can be rebuilt on its own.
    registry = ({k: v for k, v in
                 lib.read_json(lib.OUT / "contracts.json", {}).items()
                 if v["citation"] != args.case} if args.case else {})
    lib.CONTRACTS.mkdir(parents=True, exist_ok=True)

    for citation, found_here in sorted(found.items()):
        # The last key is a tiebreak, so which copy of a duplicated instrument
        # wins — and therefore what everything downstream is keyed on — does not
        # depend on dictionary order.
        found_here.sort(key=lambda c: (-len(c["provisions"]), -len(c["text"]),
                                       c["sources"][0]["file"]))
        keep = []
        for c in found_here:
            dup = next((k for k in keep if same(c["norm"], k["norm"])), None)
            if dup:
                print(f"  duplicate: {c['instrument']!r} is already registered "
                      f"as {dup['instrument']!r}")
                continue
            keep.append(c)

        for c in keep:
            cid = contract_id(citation, c["sources"], registry)
            path = lib.CONTRACTS / f"{cid}.md"
            path.write_text(c["text"], encoding="utf-8")
            registry[cid] = {
                "citation": citation, "instrument": c["instrument"],
                "file": str(path.relative_to(lib.ROOT)).replace("\\", "/"),
                "sources": c["sources"], "note": c["note"],
                "chars": len(c["text"]), "lines": c["text"].count("\n") + 1,
                "provisions": c["provisions"],
            }
            print(f"  {cid}: {len(c['text']):,} chars, "
                  f"{len(c['provisions'])} provisions")

    lib.write_json(lib.OUT / "contracts.json", registry)
    print(f"{len(registry)} instruments registered")


if __name__ == "__main__":
    main()

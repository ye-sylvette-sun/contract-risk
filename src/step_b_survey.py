"""Step B — instrument survey + provision inventory (one LLM call per OCR file).

Two questions about one file, answered in one call because they read the same
text: is there an instrument in here and where does it start and end, and what
are its provisions in order. The model returns a line window and the repaired
text for each provision; verify() checks the two against each other.

Input : output/cases.json, bloomberg_datalab/<bundle>/*.md
Output: output/survey.json  (resumable — re-runs only what is missing)

Usage:
    conda run -n legal-llm python src/step_b_survey.py [--case CITATION]
"""
import argparse

import lib

OUT = lib.OUT / "survey.json"
EDGE = 300             # characters of each sibling file shown, per end


def siblings(paths, me):
    """The other files in the bundle, by name and by how they open and close.

    This is what lets the model say a file continues another one: it cannot see
    their contents, but it can see their edges, and filenames do not sort
    ("144 pages.md" before "Declaration Part 2.md").
    """
    others = [p for p in paths if p != me]
    if not others:
        return "This is the only file in its bundle."
    out = ["OTHER FILES IN THIS BUNDLE, with how each one opens and closes:"]
    for p in others:
        t = " ".join(lib.strip_ocr(p.read_text(encoding="utf-8")).split())
        head, tail = t[:EDGE], t[-EDGE:] if len(t) > EDGE else ""
        out.append(f"\n--- {p.name}  ({len(t):,} characters)\n"
                   f"    OPENS: {head}\n    CLOSES: {tail}")
    return "\n".join(out)


def keep(answer, lines):
    """Verified provisions, and why any were dropped."""
    lo, hi = answer["instrument_start_line"], answer["instrument_end_line"]
    kept, dropped, seen = [], [], set()
    for p in answer["provisions"]:
        start, end = p["start_line"], p["end_line"]
        if not (lo <= start <= end <= hi):
            dropped.append((p["name"], f"lines {start}-{end} lie outside the "
                                       f"instrument ({lo}-{hi})"))
            continue
        why = lib.verify(lines, start, end, p["text"])
        if why:
            dropped.append((p["name"], why))
            continue
        key = " ".join(p["text"].split()).lower()
        if key in seen:                      # the same provision reported twice
            dropped.append((p["name"], "duplicate of an earlier provision"))
            continue
        seen.add(key)
        kept.append({"name": p["name"], "lines": [start, end],
                     "text": p["text"], "repairs": p["repairs"]})
    return kept, dropped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", help="survey one citation's bundles only")
    args = ap.parse_args()

    cases = lib.read_json(lib.OUT / "cases.json", {})
    done = lib.read_json(OUT, {})

    for citation, case in cases.items():
        if args.case and citation != args.case:
            continue
        for bundle in case["bundles"]:
            paths = sorted((lib.DATALAB / bundle).glob("*.md"))
            for path in paths:
                rel = f"{bundle}/{path.name}"
                if rel in done:
                    continue
                text = lib.strip_ocr(path.read_text(encoding="utf-8"))
                lines = text.split("\n")
                record = {"citation": citation, "bundle": bundle,
                          "file": path.name, "instrument_name": "",
                          "lines": None, "continues_from": "",
                          "ends_mid_document": False,
                          "provisions": [], "dropped": []}

                why = lib.out_of_bounds(text)
                if why:
                    record["note"] = f"skipped: {why}"
                    print(f"skip   {rel}  ({why})")
                else:
                    print(f"survey {rel}  ({len(text):,} chars)")
                    answer = lib.ask("survey", lib.slug(rel, words=16),
                                     citation=citation,
                                     filename=path.name,
                                     document=lib.numbered(text),
                                     siblings=siblings(paths, path))
                    if answer is None:
                        continue
                    record["note"] = answer["note"]
                    record["continues_from"] = answer["continues_from"]
                    record["ends_mid_document"] = answer["ends_mid_document"]
                    if not answer["instrument_name"]:
                        print(f"  no instrument: {answer['note']}")
                    else:
                        lo = answer["instrument_start_line"]
                        hi = answer["instrument_end_line"]
                        if not 1 <= lo <= hi <= len(lines):
                            record["note"] += (f"  [rejected: instrument lines "
                                               f"{lo}-{hi} outside 1-{len(lines)}]")
                            print(f"  ! instrument lines {lo}-{hi} outside the file")
                        else:
                            kept, dropped = keep(answer, lines)
                            record.update(instrument_name=answer["instrument_name"],
                                          lines=[lo, hi], provisions=kept,
                                          dropped=[{"name": n, "reason": r}
                                                   for n, r in dropped])
                            print(f"  {answer['instrument_name']}: lines {lo}-{hi}, "
                                  f"{len(kept)} provisions ({len(dropped)} dropped)")
                            for n, r in dropped:
                                print(f"    - dropped {n!r}: {r}")
                            if answer["continues_from"]:
                                print(f"    continues {answer['continues_from']!r}")
                done[rel] = record
                lib.write_json(OUT, done)

    found = sum(1 for r in done.values() if r["lines"])
    print(f"{len(done)} files surveyed | {found} contain an instrument")


if __name__ == "__main__":
    main()

"""Step A — case triage (one LLM call per case and key).

Did the court construe language from the parties' own instrument, and is the
dispute the risk type that Westlaw key denotes? The answer steers download
priority only; nothing from this step reaches the dataset.

Input : output/cases.json, output/opinions/<id>.txt
Output: output/triage.json  (resumable — re-runs only what is missing)

Usage:
    conda run -n legal-llm python src/step_a_triage.py [--case CITATION]
"""
import argparse

import lib

OUT = lib.OUT / "triage.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", help="run one citation only")
    args = ap.parse_args()

    cases = lib.read_json(lib.OUT / "cases.json", {})
    done = lib.read_json(OUT, {})
    todo = [(cit, c) for cit, c in cases.items()
            if not args.case or cit == args.case]
    if args.case and not todo:
        raise SystemExit(f"{args.case} is not in cases.json")

    for citation, case in todo:
        opinion = (lib.OPINIONS / f"{case['id']}.txt").read_text(encoding="utf-8")
        lines = opinion.split("\n")
        for key, headnotes in case["keys"].items():
            call_id = f"{case['id']}_{lib.slug(key)}"
            if call_id in done:
                continue
            _folder, code, about = lib.KEY_BY_LABEL[key]
            print(f"triage {citation} {key}")
            answer = lib.ask(
                "triage", call_id,
                citation=citation, opinion=lib.numbered(opinion),
                key=key, about=about, code=code, type=lib.TYPES[code],
                headnotes="\n".join(f"- {h}" for h in headnotes))
            if answer is None:
                continue

            start = answer["opinion_comment_start_line"]
            end = answer["opinion_comment_end_line"]
            # Opinions are clean text from case.law: nothing to repair, nothing
            # to align. The range only has to be inside the file.
            ok = 1 <= start <= end <= len(lines)
            if not ok and (start or end):
                print(f"  ! line range {start}-{end} is outside the opinion "
                      f"(1-{len(lines)})")
            done[call_id] = {
                "citation": citation, "key": key, "taxonomy": code,
                "usable": answer["usable"], "reason": answer["reason"],
                "lines": [start, end] if ok else None,
                "passage": lib.window(lines, start, end) if ok else "",
            }
            lib.write_json(OUT, done)

    usable = sum(1 for r in done.values() if r["usable"])
    print(f"{len(done)} (case, key) pairs judged | {usable} usable")


if __name__ == "__main__":
    main()

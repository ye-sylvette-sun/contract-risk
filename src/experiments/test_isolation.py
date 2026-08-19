"""What the confinement hook must and must not refuse.

The Glob-pattern gap was found by auditing trajectories after a paid run. These
cases run in a second and would have caught it first.

    python src/experiments/test_isolation.py
"""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import isolation  # noqa: E402

# (tool, tool_input, should_be_denied, why this case exists)
CASES = [
    # --- the ordinary work of a session, which must keep working -------------
    ("Read", {"file_path": "contract.txt"}, False, "the contract itself"),
    ("Read", {"file_path": "examples/1.1_x/notes.md"}, False, "a worked example"),
    ("Write", {"file_path": "predictions_001.json"}, False, "the deliverable"),
    ("Glob", {"pattern": "examples/*/notes.md"}, False, "the commonest glob in the logs"),
    ("Glob", {"pattern": "*"}, False, "listing the workspace"),
    ("Glob", {"pattern": "**/*"}, False, "listing it recursively"),
    ("Glob", {"pattern": "**/notes.md", "path": "."}, False, "pattern plus explicit cwd"),
    ("Grep", {"pattern": "indemnif", "path": "contract.txt"}, False,
     "Grep's `pattern` is CONTENT, and its path is inside — must not be refused"),
    ("Grep", {"pattern": "termination", "path": "."}, False, "search the workspace"),

    # --- the gap this file exists for ---------------------------------------
    ("Glob", {"pattern": "/tmp/**/contract.txt"}, True, "seen in a real trajectory"),
    ("Glob", {"pattern": "/home/**/contract.txt"}, True, "seen in a real trajectory"),
    ("Glob", {"pattern": "/mnt/**/contract.txt"}, True, "seen in a real trajectory"),
    ("Glob", {"pattern": "/*/*/contract.txt"}, True, "seen in a real trajectory"),
    ("Glob", {"pattern": "/**/35FSupp3d725*/contract.txt"}, True,
     "seen in a real trajectory: hunting another workspace by contract id"),
    ("Glob", {"pattern": "/**/dataset.csv"}, True, "the answer key, four levels up"),
    ("Glob", {"pattern": "../../*.csv"}, True, "traversal by relative pattern"),

    # --- the paths that were already covered, kept so they stay covered ------
    ("Read", {"file_path": "../../dataset.csv"}, True, "the answer key by traversal"),
    ("Read", {"file_path": "/etc/passwd"}, True, "absolute, outside"),
    ("Read", {"file_path": "/mnt/user-data/outputs/provisions.json"}, True,
     "seen in a real trajectory"),
    ("Read", {"file_path": "/tmp/outputs/contract.txt"}, True, "seen in a real trajectory"),
    ("Grep", {"pattern": "POSITIVE", "path": "../../dataset.csv"}, True,
     "grepping the gold directly"),
    ("Glob", {"pattern": "*", "path": "../.."}, True, "safe pattern, escaping path"),
]


async def main():
    root = Path(tempfile.mkdtemp(prefix="confine_test_"))
    (root / "examples" / "1.1_x").mkdir(parents=True)
    failures = []

    for tool, ti, want_denied, why in CASES:
        denials = []
        hook = isolation.confine(root, denials)
        out = await hook({"tool_name": tool, "tool_input": ti}, None, None)
        denied = bool(out.get("hookSpecificOutput", {}).get("permissionDecision")
                      == "deny")
        ok = denied == want_denied
        if not ok:
            failures.append((tool, ti, want_denied, denied, why))
        print(f"  {'ok  ' if ok else 'FAIL'}  "
              f"{'DENY' if denied else 'allow'}  {tool} {ti}   # {why}")
        # A denial that is not recorded is a denial nobody can audit.
        if denied and not denials:
            failures.append((tool, ti, "recorded", "not recorded", why))

    print()
    if failures:
        for tool, ti, want, got, why in failures:
            print(f"FAIL {tool} {ti}: wanted {want}, got {got}  ({why})")
        print(f"\n{len(failures)} FAILURE(S)")
        return 1
    print(f"{len(CASES)} cases pass — the workspace boundary holds for every "
          f"path-shaped argument the four tools take")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

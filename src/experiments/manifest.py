"""What the machine was, when a run is read back a year later.

One `run_manifest.json` per invocation, beside the run's logs, written TWICE:
before the first session so a run that dies still says what it was, and again at
the end with the outcome.

Deliberately absent: the VALUES of environment variables. Only names are
recorded — a manifest that leaks an API key would be worse than the problem it
solves. Used by both experiments, so they are comparable on provenance too.
"""
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from pathlib import Path

PACKAGES = ("claude-agent-sdk", "anthropic", "matplotlib", "openpyxl")


def _utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _run(cmd, cwd=None):
    """A command's first line of output, or None. Never raises."""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                             cwd=cwd, check=False)
        return (out.stdout or out.stderr).strip().splitlines()[0] if (
            out.stdout or out.stderr).strip() else None
    except Exception:                                            # noqa: BLE001
        return None


def _cli():
    """The CLI the SDK will actually spawn, not whatever `which claude` finds.

    The SDK ships a CLI inside its own wheel and prefers it to anything on PATH
    (`_find_cli` checks `_find_bundled_cli` first). On the machine that ran the
    preflight, PATH had 2.1.227 from Homebrew and the SDK spawned the bundled
    2.1.233 — so `claude --version`, which is what this recorded at first, would
    have put a version in the manifest that never ran a single session.

    That is good news for pinning: `claude-agent-sdk==<x>` pins the CLI too. It
    is only bad news for a manifest that asks the wrong process.
    """
    path = None
    try:
        from claude_agent_sdk._internal.transport.subprocess_cli import (
            SubprocessCLITransport)
        path = SubprocessCLITransport.__new__(SubprocessCLITransport)._find_cli()
    except Exception:                                            # noqa: BLE001
        path = shutil.which("claude")
    return {"cli_path": path,
            "cli_version": _run([path, "--version"]) if path else None,
            "cli_on_path": shutil.which("claude")}


def _versions():
    import importlib.metadata as md
    got = {}
    for p in PACKAGES:
        try:
            got[p] = md.version(p)
        except Exception:                                        # noqa: BLE001
            got[p] = None
    return got


def sha256(path):
    """A file's digest, or None if it is not there."""
    path = Path(path)
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def options_digest(options):
    """A ClaudeAgentOptions as JSON, with callbacks named rather than dropped.

    The point of recording the options is to show what isolation was actually
    requested, so a field that cannot be serialised is replaced by a marker
    instead of being silently omitted — an absent field and a field set to a
    function look identical otherwise, and one of those is a hole in the record.
    """
    if not is_dataclass(options):
        return options
    out = {}
    for f in fields(options):
        v = getattr(options, f.name)
        if v is None or isinstance(v, (str, int, float, bool)):
            out[f.name] = v
        elif isinstance(v, (list, tuple)) and all(
                isinstance(x, str) for x in v):
            out[f.name] = list(v)
        elif f.name == "hooks" and v:
            out[f.name] = {k: [m.matcher for m in ms] for k, ms in v.items()}
        elif isinstance(v, dict):
            out[f.name] = {k: (x if isinstance(x, (str, int, float, bool, type(None)))
                               else f"<{type(x).__name__}>") for k, x in v.items()}
        elif isinstance(v, Path):
            out[f.name] = str(v)
        elif v:
            out[f.name] = f"<{type(v).__name__}>"
        else:
            out[f.name] = v
    return out


class Manifest:
    """Provenance for one run. `path` is the file it keeps rewriting."""

    def __init__(self, path, run, repo_root, **fields_):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data = {
            "run": run,
            "started_utc": _utc(),
            "finished_utc": None,
            "python": sys.version.replace("\n", " "),
            "executable": sys.executable,
            "platform": platform.platform(),
            "packages": _versions(),
            **_cli(),
            "git_commit": _run(["git", "rev-parse", "HEAD"], cwd=repo_root),
            "git_dirty": bool(_run(["git", "status", "--porcelain"],
                                   cwd=repo_root)),
            **fields_,
        }
        self.write()

    def hash_inputs(self, **paths):
        self.data["input_sha256"] = {k: sha256(v) for k, v in paths.items()}
        return self

    def hash_prompts(self, *paths):
        self.data["prompt_sha256"] = {Path(p).name: sha256(p) for p in paths}
        return self

    def record_env(self, removed, flags):
        """Names only. Values are never recorded, here or anywhere.

        `flags` is the caller's own HERMETIC dict rather than a list repeated
        here, so a variable renamed in one place cannot go unrecorded in the
        other — which is how `DISABLE_NON_ESSENTIAL_MODEL_CALLS`, a name the CLI
        does not have, sat in this manifest looking as though it had been set.
        What is read back is `os.environ`, not the dict: the record says what the
        process actually carries.
        """
        self.data["env_removed"] = list(removed)
        self.data["env_set"] = {k: os.environ.get(k) for k in flags}
        return self

    def set(self, **fields_):
        self.data.update(fields_)
        return self

    def finish(self, **fields_):
        self.data.update(fields_)
        self.data["finished_utc"] = _utc()
        self.write()
        return self

    def write(self):
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2,
                                        default=str), encoding="utf-8")

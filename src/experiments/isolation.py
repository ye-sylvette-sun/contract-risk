"""What a judging session may see. Shared by the host driver and the container.

Layers, weakest to strongest: DENIED_TOOLS (named, not merely absent, so a
refusal shows in the transcript), confine() (a PreToolUse hook), env() (sweep
what could steer the CLI), and the container itself — where the gold labels and
the other contracts are simply not on the filesystem. The last is what makes the
rest belt-and-braces rather than load-bearing.
"""
import os
import re
from pathlib import Path

TOOLS = ["Read", "Grep", "Glob", "Write"]

DENIED_TOOLS = ["Bash", "BashOutput", "KillShell", "Edit", "NotebookEdit",
                "WebFetch", "WebSearch", "Task", "Skill", "TodoWrite",
                "SlashCommand"]

# Every tool input that can name a location. `pattern` and `glob` were once
# missing, and an audit found sessions globbing `/tmp/**`, `/*/*/contract.txt`
# and `/**/<other-contract>*/contract.txt` unchecked — nothing escaped, but only
# because they hit "No files found" or ripgrep's timeout. `resolve()` treats a
# wildcard as an ordinary segment, so confining patterns costs nothing.
PATH_KEYS = ("file_path", "path", "notebook_path", "pattern", "glob")


def confine(root, denials):
    """PreToolUse hook: refuse any path outside the workspace, and record it."""
    base = Path(root).resolve()

    async def hook(input_data, tool_use_id, context):
        ti = input_data.get("tool_input") or {}
        for key in PATH_KEYS:
            raw = ti.get(key)
            if not raw:
                continue
            target = Path(str(raw))
            target = (base / target).resolve() if not target.is_absolute() \
                else target.resolve()
            if target != base and base not in target.parents:
                denials.append({"tool": input_data.get("tool_name"), key: str(raw)})
                return {"hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason":
                        f"{raw} is outside the workspace for this contract. "
                        f"Everything you need is in the working directory."}}
        return {}

    return hook


# ---------------------------------------------------------- environment ----
DENY = re.compile(r"^(ANTHROPIC_|CLAUDE_|CLAUDECODE|DISABLE_)", re.I)
PROXY = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}

# The subscription credential matches DENY and must survive the sweep.
KEEP = {"CLAUDE_CODE_OAUTH_TOKEN"}

# Set after the sweep. None of these sits under `setting_sources`, so none is
# covered by the options in agent_options().
HERMETIC = {
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
    # The CLI's own side-calls. Without this an earlier run billed
    # `claude-haiku-4-5` in all 64 sessions, so the arm was not one model.
    # `DISABLE_NON_ESSENTIAL_MODEL_CALLS` is NOT a name the CLI knows.
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "DISABLE_AUTOUPDATER": "1",
    "DISABLE_TELEMETRY": "1",
    "DISABLE_ERROR_REPORTING": "1",
}


def env():
    """Sweep the environment the CLI will inherit; returns the names removed.

    Must happen in the parent: `ClaudeAgentOptions.env` merges OVER the
    inherited environment and can only add. `DISABLE_PROMPT_CACHING` is swept
    and deliberately not set back. Only names are returned, never values.
    """
    removed = sorted(k for k in os.environ
                     if k not in KEEP and (DENY.match(k) or k.upper() in PROXY))
    for k in removed:
        os.environ.pop(k, None)
    os.environ.update(HERMETIC)
    return removed


def agent_options(root, system_prompt, resume, denials, model, effort, max_turns):
    """The options every session runs under.

    `setting_sources=[]` is the SDK's isolation mode; `None` would load user,
    project and local settings plus every CLAUDE.md above the workspace.
    `skills=[]` is skills off; `None` leaves the CLI's defaults in place.
    `system_prompt` as a plain string REPLACES Claude Code's preset, so no
    preamble naming the working directory is ever assembled — which is why the
    session looks identical from any absolute path.
    """
    from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

    return ClaudeAgentOptions(
        model=model,
        effort=effort,
        cwd=str(root),
        resume=resume,
        system_prompt=system_prompt,
        tools=list(TOOLS),
        allowed_tools=list(TOOLS),
        disallowed_tools=list(DENIED_TOOLS),
        permission_mode="acceptEdits",
        max_turns=max_turns,
        setting_sources=[],
        skills=[],
        strict_mcp_config=True,
        hooks={"PreToolUse": [HookMatcher(matcher=t, hooks=[confine(root, denials)])
                              for t in TOOLS]},
    )

# Reproducibility review of the agentic run — findings and verdicts

A review by Codex raised the claim that the agent experiment "creates a fresh
conversation for each contract, but does not create a clean, reproducible Claude
Code environment." Each claim below was checked against the installed SDK
(`claude-agent-sdk` 0.2.110) and against all 64 committed trajectories.

**Bottom line.** The core criticism is correct and important: `setting_sources=None`
does the opposite of what our code comment claims, so the run is *not specified*
as hermetic. But two of the reviewer's specific claims are wrong, and the
published run is *provably* uncontaminated rather than merely unproven — the
machine happened to have no `CLAUDE.md`, no project settings, and no memory for
those workspaces. We were lucky, not careful. The fix is four lines.

---

## Verdict summary

| # | Concern | Verdict | Affected this run? |
|---|---|---|---|
| 1 | `setting_sources=None` loads user/project/local settings; our comment says the reverse | **TRUE — real bug** | No (nothing to load) |
| 2 | `skills=None` is not "skills off" | **TRUE** | No |
| 3 | Auto memory is default-on and not disabled | **TRUE as a spec gap** | No — proven absent |
| 4 | Memory is keyed by git repo, so all subdirectories share it | **FALSE** | No |
| 5 | "Not proven contaminated, but not established uncontaminated" | **HALF TRUE** | Contamination is disprovable, and disproved |
| 6 | `env()` inherits the whole environment, removing only 3 variables | **TRUE** | Unknown, low risk |
| 7 | One `Bash` and one `Edit` call prove the tool restriction failed | **FALSE** | No — both were denied |
| 8 | `claude-agent-sdk` absent from `requirements.txt`; nothing pinned | **TRUE** | N/A |
| 9 | CLI version recorded, Python environment not | **TRUE** | N/A |
| 10 | One run per condition; the bootstrap measures across-contract variance | **TRUE** | Yes — already disclosed |
| 11 | Proposed fix `env={allowlisted only}` | **The fix does not work as written** | N/A |

---

## 1. `setting_sources=None` — TRUE, and the decisive finding

Our code says:

```python
# Hermetic: no user or project settings, so the run does not depend on
# whatever is in ~/.claude at the time.
setting_sources=None,
```

The SDK docstring in `claude_agent_sdk/types.py:1855` says the opposite:

> When `None`, all sources are loaded (matches CLI defaults). Pass `[]` to
> disable filesystem settings (SDK isolation mode). Must include `"project"` to
> load CLAUDE.md files.

**The comment is false and the code did the opposite of its stated intent.** User
settings, project settings, local settings and `CLAUDE.md` files were all
eligible to load in every one of the 64 sessions.

**Fix.** `setting_sources=[]`. Our SDK is 0.2.110, well past the 0.1.59 the
reviewer notes as the floor for `[]` behaving correctly.

## 2. `skills=None` is not "skills off" — TRUE

Same file, `types.py:1867`: `None` means "no SDK auto-configuration. The CLI's
own defaults still apply, so this is **not** 'skills off'." Our `~/.claude/settings.json`
does register a skills marketplace (`anthropic-agent-skills`), so skills were
discoverable in principle.

No effect on this run: `Skill` was not in `allowed_tools`, and no skill or
`mcp__*` call appears in any trajectory. **Fix:** `skills=[]`.

## 3. Auto memory not disabled — TRUE as a specification gap, but disproved for this run

Auto memory sits outside `setting_sources` and is on by default. We never set
`CLAUDE_CODE_DISABLE_AUTO_MEMORY`. The reviewer is right that the code does not
rule memory out.

**But it can be checked, and it is clean.** Memory and `CLAUDE.md` are injected
into the first user turn inside `<system-reminder>` blocks, and user turns *are*
recorded in the trajectories. Searching all 64:

| probe | files matched |
|---|---|
| `MEMORY.md` | **0 / 64** |
| `conda run`, `newline fails`, `Prefers concise`, `drawio` (distinctive strings from this machine's MEMORY.md) | **0 / 64** each |
| `CLAUDE.md` | **0 / 64** |
| `skills are available` / `Skill tool` | **0 / 64** |

The 18 files containing `system-reminder` at all are the CLI's own wrapper around
`Read` results — contract text and `provisions.json`, nothing else.

The reason nothing loaded is that there was nothing to load: no `CLAUDE.md`
anywhere in the workspace's ancestor chain, no `~/.claude/CLAUDE.md`, no
`.claude/` directory in the project tree, and `~/.claude/settings.json` contains
only a marketplace registration, `effortLevel`, and UI preferences — no hooks, no
permissions, no `env`. **That is luck, not isolation**, and it would not hold on
another machine. **Fix:** set `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` and
`CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` in the parent process before spawning.

## 4. Memory keyed by the git repository — FALSE

The claim: "Claude Code derives its memory location from the Git repository, and
all subdirectories in the repo share that memory."

Not how it behaves here. Project state is keyed by the **absolute cwd path**.
Each of the 64 workspaces got its own directory under `~/.claude/projects/`, e.g.
`C--Users-...-_github_repo-output-exp3-agent-ws-<cid>`. Of 90 project
directories on this machine, exactly **3** contain a `memory/` folder, and all
three are top-level working directories — none is a `exp3_agent_ws` workspace.
There is no shared repo-level memory store for the agent workspaces to read.

## 5. "Not proven uncontaminated" — HALF TRUE

Correct that the *implementation* establishes nothing. Incorrect that the logs
cannot settle it: §3 above settles it for memory, `CLAUDE.md`, and skills by
direct search of the recorded transcripts.

What the trajectories genuinely **cannot** rule out is anything that reaches the
CLI without appearing in the conversation — `settings.json` values, hooks,
environment variables, and managed organizational policy. The transcript records
no system prompt and no instruction manifest. So the honest statement is: *the
prompt-visible context is verified clean; the process-level configuration is
not verifiable from the published artefacts.*

## 6. The environment is inherited whole — TRUE

`env()` pops exactly three variables (`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`,
`DISABLE_PROMPT_CACHING`) and the SDK then spawns the CLI with the remainder of
`os.environ`. Any `CLAUDE_*` or proxy variable set on the operator's machine
would have carried into the run. We have no record of what was set, so this is
unfalsifiable after the fact — which is itself the problem.

## 7. Bash and Edit calls prove the restriction failed — FALSE

The reviewer's parse is right that the calls exist; the conclusion is wrong. A
census of all 755 tool calls in the 64 trajectories:

```
Read 470   Write 170   Glob 92   Grep 21   Edit 1   Bash 1
```

Both out-of-scope calls returned `is_error=True`:

> `Error: No such tool available: Edit. Edit exists but is not enabled in this context.`
> `Error: No such tool available: Bash. Bash exists but is not enabled in this context.`

**The restriction held.** The model attempted two calls outside its toolset and
was denied both; in the `Edit` case it fell back to rewriting the file with
`Write`. The executed harness matches the committed one — this is evidence *for*
the harness, not against it. No remediation needed.

## 8. Dependencies unpinned — TRUE

The committed `_github_repo/requirements.txt` contains only:

```
anthropic>=0.120
openpyxl
```

`claude-agent-sdk` is declared in a `requirements.txt` one directory **above** the
repo, which is not committed. So the repo cannot install its own agent
experiment. Both files use `>=` floors, not pins.

**Fix.** Add to the repo's `requirements.txt`, pinned to what actually ran:
`claude-agent-sdk==0.2.110`, `anthropic==<exact>`, `matplotlib==<exact>`, and
record the CLI version alongside.

## 9. CLI version recorded, Python environment not — TRUE

Every trajectory line carries `version=2.1.191` (consistent across all 2,027
records) plus `gitBranch=main` and the absolute `cwd`. That is more provenance
than the reviewer credits, but it covers only the CLI. The Python SDK version,
interpreter, and OS are absent. **Fix:** write a `run_manifest.json` per run —
SDK version, CLI version, `sys.version`, platform, model, effort, prompt hash,
input hashes, contract order, and the allowlisted environment.

## 10. Single run per condition — TRUE, and already disclosed

Correct, and it is the honest limit on the result. Our contract-level bootstrap
measures variation across contracts, not across repeated executions, so it cannot
speak to run-to-run stochasticity. This is already stated in the report's
limitations. Note the effect sizes it bears on: ΔROC +0.046 (95% CI
[+0.001, +0.092]) and ΔPR +0.132 (95% CI [+0.006, +0.258]) — both clear zero
only narrowly, so run-to-run variance genuinely matters here.

## 11. The proposed `env=` fix does not do what it says — FALSE as written

The review recommends:

```python
env={  # deliberately allowlisted variables only
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
}
```

`ClaudeAgentOptions.env` is **not** an allowlist. In
`_internal/transport/subprocess_cli.py:431` the SDK builds:

```python
process_env = {**inherited_env, "CLAUDE_CODE_ENTRYPOINT": "sdk-py",
               **self._options.env, "CLAUDE_AGENT_SDK_VERSION": __version__}
```

`options.env` merges *over* the full inherited environment. Passing it sets those
two variables correctly — which is the part that matters — but does not restrict
inheritance. Restricting inheritance requires sanitising `os.environ` in the
parent process (extending what `env()` already does) or running in a clean
container or OS user.

---

## Remediation

**Do now — four lines, no rerun required.** Correct the specification so the
harness is defensible and any future run is isolated by construction:

```python
setting_sources=[],        # was None, which loads user + project + local
skills=[],                 # None is not "off"
strict_mcp_config=True,    # ignore project/user/plugin MCP config
env={"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
     "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1"},
```

and fix the false comment above `setting_sources`.

**Do now — provenance.** Pin `claude-agent-sdk==0.2.110` and the other deps in
the repo's own `requirements.txt`, and emit a per-run `run_manifest.json`
(§9).

**Argue, do not rerun — the published result.** The prompt-visible context of all
64 sessions is verified free of memory, `CLAUDE.md`, and skills (§3), the tool
restriction is verified to have held (§7), and the CLI version is uniform across
every record (§9). The residual exposure is process-level configuration on one
machine, which cannot be reconstructed after the fact. That is a fair caveat to
state in the limitations rather than grounds to discard the run.

**Optional, if cheap.** A single clean-container replication of a handful of
contracts under the corrected options would convert the §3 argument from
"verified from logs" to "verified by construction". Full paired repetitions on
independent machines are the rigorous answer, but for the claim we are actually
making — the agent ranks better on average — the corrected specification plus the
existing log evidence is proportionate.

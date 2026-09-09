# Reproducibility of the agent experiment

The agent run starts a fresh conversation for each contract. That is not enough
on its own: the Claude Code CLI can load settings files, `CLAUDE.md`, memory,
skills and MCP servers that never appear in the conversation, and it runs on a
filesystem that holds the answers. This document sets out what the run is
isolated from, how each guarantee is enforced, and how it is checked.

---

## 1. What the session is isolated from

**Configuration.** The SDK treats `None` as "load every source, matching CLI
defaults" — user `~/.claude/settings.json`, project `.claude/settings.json`,
local `.claude/settings.local.json`, and every `CLAUDE.md` on the ancestor chain.
The run passes explicit empty values instead:

```python
setting_sources=[]          # no settings file of any scope
skills=[]                   # no skills; None would mean "configure nothing"
strict_mcp_config=True      # no project, user or plugin MCP server
```

**Memory and `CLAUDE.md` injection** sit outside `setting_sources` and are
switched off by environment variable: `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` and
`CLAUDE_CODE_DISABLE_PROJECT_CLAUDE_MD=1`.

**The environment.** `env()` sweeps the parent process and removes by name every
variable that could reach the CLI — API keys, base URLs, proxy settings, the
model override, telemetry, the lot — rather than removing a known few. What is
passed in is what the experiment chose to pass in.

**Non-essential traffic.** `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`. Without
it a session bills a second, smaller model for background work, and the run is
then not a single-model measurement.

**Self-update.** `DISABLE_AUTOUPDATER=1`, so the CLI cannot change version
mid-run.

**Tools.** Four tools are allowed and the rest are named in an explicit
`disallowed_tools` list, so a tool absent from the allow-list is refused rather
than merely unmentioned.

**The filesystem.** The session has no `Bash`, but `Read` and `Glob` take paths.
A `PreToolUse` hook resolves every path argument against the workspace root and
denies anything outside it. Each denial is recorded, and `preflight.py` reports
the count.

**The machine.** Every session runs in its own container from an image pinned by
digest, not by tag:

```
contract-risk-judge:0.2.139
sha256:b5f50d7dc71f6eae1ce623fbbcad0853e927ce6c564ae44d9ff53015f1bb1fec
```

The container has no `~/.claude`, no settings, no skills, no managed policy, and
only this contract's workspace is mounted. `dataset.csv` and the other contracts
are not on its filesystem at all.

**Credentials.** `CLAUDE_CODE_OAUTH_TOKEN` is passed as a single environment
variable — a subscription credential, never an API key — so authentication
enters the container without bringing any configuration with it.

**The evaluation set.** Every contract filed in an example's case is held out,
not just the example contract itself. A case's other documents are mounted as
`context/`, so judging a sibling of an example would put that example's own
construed provisions and the court's words about them in front of the model.

---

## 2. What is pinned

All dependencies are pinned with `==` and committed before the run, including
`claude-agent-sdk`. The CLI version is resolved through the SDK's own lookup
rather than from `PATH`: the SDK ships a CLI and will prefer it, so the version
on `PATH` is not necessarily the one that runs.

`claude-opus-5` is an alias rather than a dated snapshot, and the API returns
the alias in its usage records. The exact weights behind it on a given day are
not recoverable from the logs; the manifest records what was asked for and what
was billed, which is as far as the alias allows.

---

## 3. What is logged, and where

```
output/llm_logs/risk_detect_agent/<cid>.json             request, response, usage
output/llm_logs/risk_detect_agent/<cid>.trajectory.jsonl every turn of the session
output/risk_detect_agent/<cid>.json                      the judgments returned
output/risk_detect_agent_ws/<cid>/                       the workspace as mounted
output/run_manifest_<timestamp>.json                     image digest, CLI version,
                                                         model asked for and billed,
                                                         options, dependency versions
```

The trajectory is the record that matters for isolation: it shows every tool
call the session made, so a claim that the model read only what it was given can
be checked rather than assumed.

---

## 4. How the claims are checked

`preflight.py` runs one contract end to end and asserts fourteen properties of
the result before a full run is worth starting:

```
TRAJECTORY   the trajectory exists and has records
CONTAINER    the image and working directory are the pinned ones
MEMORY       no memory file was loaded
CLAUDE_MD    no CLAUDE.md was injected
SKILLS       no skill was loaded
REMINDERS    no system reminders wrapped the tool results
CLI          the CLI version in the trajectory is the one the SDK spawns
MODEL        the model billed is the model asked for, and only that model
PATHS        how many path arguments fell outside the workspace, and how many
             were refused
TOOLS        which tools were actually used
MANIFEST     the run manifest was written
OPTIONS      setting_sources, skills and strict_mcp_config are the isolating values
ENV          how many variables were swept, and that every flag is set
```

A failure here is a reason not to run, not a warning to note.

---

## 5. Known limits

- **The model alias is not a snapshot.** A repeat months later may run different
  weights under the same name, and nothing in the logs would show it.
- **Run-to-run variance is unmeasured.** No seed, no temperature control, and
  the same prompt has never been run twice on this dataset. Two configurations
  cannot be compared until it has been; `compare_risk_detect.py --against`
  exists for that.
- **The clause-to-passage link in the dataset is a model's judgment**, not a
  verified fact — see [DATASET.md](DATASET.md) §5. Isolation makes the *run*
  reproducible; it does not make the labels correct.

---

## 6. Reproducing the run

```bash
docker build -f docker/Dockerfile -t contract-risk-judge:0.2.139 .
export CLAUDE_CODE_OAUTH_TOKEN=...          # subscription credential

python src/experiments/preflight.py         # must pass all fourteen
python src/experiments/risk_detect_agent.py --parallel 6

python src/experiments/compare_risk_detect.py
python src/experiments/plot_risk_detect_thresholds.py

python src/experiments/issue_alignment_check.py --parallel 6
python src/experiments/issue_alignment_check.py --control case --out DIR
python src/experiments/plot_issue_alignment_thresholds.py
```

Rebuilding the dataset from the corpus:

```bash
python src/step1_inventory.py  --parallel 6
python src/step2_disputes.py   --parallel 6
python src/step3_issue_scope.py --parallel 6
python src/build_dataset.py
```

`step2_disputes.py` and `step3_issue_scope.py` resume: a case already in the
artifact is skipped. `--cases-file PATH` runs exactly the citations listed in a
file, one per line, and fails loudly on a citation that matches no inventoried
case.

# Agent 实验的可复现性 · 说明（中文）

配套文档：[数据集说明](DATASET_ZH.md)、[实验说明](EXPERIMENTS_ZH.md)。英文原文见
[REPRODUCIBILITY.md](REPRODUCIBILITY.md)。

外部 review 指出：agent 实验为每份合同开了新的**对话**，但没有建立干净的**运行环境**。
本文逐条列出 review 提出的问题、各自的处理，以及验证方式。

只有 **agent** 一路受影响。`exp3_llm_api.py` 是无状态 Messages API 调用，不涉及 CLI、
配置文件、memory 或文件系统，其预测结果未重跑。

---

## 一、问题与处理

- **`setting_sources=None` 会加载全部配置文件。** SDK 对 `None` 的定义是"加载全部来源，
  与 CLI 默认一致"：`~/.claude/settings.json`、`.claude/settings.json`、
  `.claude/settings.local.json`，以及工作目录各级祖先目录下的 `CLAUDE.md`。原代码的
  注释写的是相反的意思。
  **修复：**`setting_sources=[]`（SDK 隔离模式），并删除该注释。`ClaudeAgentOptions`
  是 dataclass，不存在的选项名会在构造时抛 `TypeError`，因此 session 能启动即表示这些
  选项均有效。

- **`skills=None` 不等于关闭 skills。** `None` 表示 SDK 不做配置，CLI 自身默认仍生效，
  包括用户配置中注册的 skills marketplace。
  **修复：**`skills=[]`。

- **项目级、用户级、插件级 MCP server 仍可被加载。**
  **修复：**`strict_mcp_config=True`。

- **auto memory 与 `CLAUDE.md` 注入不受 `setting_sources` 控制**，两者默认开启。
  **修复：**在 SDK 启动子进程前，于父进程设置 `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`
  与 `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`。

- **父进程环境被整体继承，`env()` 仅删除三个变量。** 机器上任何 `CLAUDE_*`、
  `ANTHROPIC_*` 或代理变量都会进入运行，且无记录。
  **修复：**`env()` 清扫父进程，按名删除匹配 `ANTHROPIC_*`、`CLAUDE_*`、`CLAUDECODE`、
  `DISABLE_*` 及代理变量名的条目，再设置上述开关。注意 review 建议的 `env={...}` 无法
  达到此效果：SDK 构造的是 `{**inherited_env, ..., **options.env, ...}`，`options.env`
  覆盖在继承环境之上，只能增不能减，限制继承必须在父进程完成。记录变量名，不记录值。
  搬进容器之后这套清扫已无可清扫之物：容器启动时没有任何 `CLAUDE_*` 或 `ANTHROPIC_*`
  变量，64 个 session 记录的清扫数量都是 **0**。代码保留，因为这个数字正是靠它才可查。

- **CLI 可能在运行中途自动升级。**
  **修复：**`DISABLE_AUTOUPDATER=1`。实际影响有限，原因见下文 CLI 版本一条。

- **agent 一路并非只运行一个模型。** 64 个 session 均额外为 CLI 内部调用计费
  `claude-haiku-4-5`。
  **修复：**`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`。注意 CLI 中不存在
  `DISABLE_NON_ESSENTIAL_MODEL_CALLS` 这个变量名，设置它无任何效果。

- **工具限制是隐式的**：`Bash`、`Edit` 只是未出现在白名单中。
  **修复：**在四工具白名单外增加显式 `disallowed_tools`，列出 `Bash`、`Edit`、`Task`、
  `Skill`、`WebFetch`、`WebSearch` 等。

- **无文件系统边界。** session 无 `Bash`，但 `Read`、`Glob` 接受绝对路径，可触及本仓库
  源码、`dataset.csv` 中的标准答案或其他合同的工作区。
  **修复：**`PreToolUse` hook 将每个路径参数相对 session 工作区解析，工作区外的一律
  拒绝并记录。检查的参数名包括 `pattern` 与 `glob`，不只是 `file_path`——`Glob` 的路径
  是走 `pattern` 传的，只看 `file_path` 的 hook 会让绝对路径的 glob 整个漏过去。
  `test_isolation.py` 用 22 个用例把这件事钉死，宿主机与镜像内都跑。64 个 session 里
  模型共有 32 次工作区外的尝试，全部是 `Read`，全部被拒绝，全部指向并不存在的位置
  （`/tmp/outputs/...`、`/mnt/user-data/outputs/...`、`/Users/you/work/...`）；每个
  session 随后都找到了真实路径并正常完成。

- **`claude-agent-sdk` 不在 `requirements.txt` 中**，已有两条依赖使用 `>=` 下界。
  **修复：**全部改为 `==` 锁定并在运行前提交：`claude-agent-sdk==0.2.139`、
  `anthropic==0.122.0`、`matplotlib==3.8.4`、`openpyxl==3.1.2`，在镜像构建时装入。
  看 manifest 时有一点要注意：它记录的是**宿主机**环境——宿主机驱动实验但不执行实验——
  已经写下的 manifest 里 `matplotlib` 都是 3.11.1，因为当时的宿主机是 Windows +
  CPython 3.13，3.8.4 在那里没有 wheel。宿主机后来已重建到 CPython 3.12，锁定的
  wheel 能装上，所以现在再写一份 manifest 会记录 3.8.4。
  真正决定 session 行为的两个版本 `claude-agent-sdk` 与 `anthropic` 处处一致；
  `matplotlib` 与 `openpyxl` 只用来画图和读表格，session 运行的代码一个都不 import。
  SDK 须高于 0.1.59，低于该版本 `setting_sources=[]` 行为不正确。

- **仅记录了 CLI 版本，机器环境无记录。**
  **修复：**每次调用在首个 session 前写入
  `output/llm_logs/exp3_agent/run_manifest_<时间戳>.json`，结束时再写一次，内容包括
  SDK 与 CLI 版本、Python 解释器、平台、git commit 与 dirty 标志、隔离选项原文、被清扫
  的环境变量名单、生效的开关、prompt 与 `dataset.csv`、`contracts.json` 的 SHA-256、
  模型、effort、选中的示范例子、合同运行顺序，以及容器的 image id 与内容哈希。每个字段
  对应 review 的哪一条要求，见第四节。文件名带时间戳而非覆盖写，因为脚本可续跑。

- **记录的 CLI 版本会是错的。** SDK 的 wheel 内置一份 CLI 并优先于 `PATH` 使用。本机
  `claude --version` 为 Homebrew 的 2.1.227，实际运行的是内置的 2.1.233。
  **修复：**manifest 通过 SDK 自身的查找逻辑解析 CLI，同时记录其路径、版本与 `PATH`
  上的版本。因此锁定 `claude-agent-sdk` 即锁定 CLI。

- **原先运行在开发机的普通用户下。** 环境清扫覆盖不到企业托管的组织级策略
  (`/etc/claude-code/managed-settings.json`)；而任何交互式用过 Claude Code 的机器，
  `~/.claude` 里都带着配置、memory 与 skills。
  **处理：** 每个 session 都在自己的容器里跑，基础镜像按 digest 锁定
  (`ubuntu@sha256:d78ab764...`)，以镜像构建时新建的非 root 用户运行，其 home 目录是空的。
  文件系统上没有托管策略文件、没有 `~/.claude`、没有 skills、没有任何 `CLAUDE.md`——
  不是被关掉了，是从来没装过。容器只挂载当前这一份合同的工作区到 `/work`，两个 prompt
  只读挂到 `/opt/task`；`dataset.csv`、本仓库以及另外 63 份合同根本不在它的文件系统上。
  镜像构建时会断言 SDK 自带的 CLI 就是 2.1.233，构建得出来的镜像不可能悄悄换了 CLI。

- **登录凭据要进容器，但不能把配置一起带进去。** 直接挂 `~/.claude` 目录会把配置、
  memory、skills 一并带入。
  **处理：** 用环境变量 `CLAUDE_CODE_OAUTH_TOKEN` 传入；没有它时，只读挂载单个文件
  `~/.claude/.credentials.json`。目录本身永不挂载。manifest 记录用了哪条路径，不记录值。

- **review 未提及、我们自查发现：两路看到的示范例子并不相同。** agent 的工作区里放了
  每个示范例子的合同全文，而 `exp3_llm_api.py` 的 few-shot 块里只有两段条款正文和法院
  的原话。也就是说 agent 手上有对照组拿不到的材料。
  **处理：**删掉工作区里的示范合同，连同 prompt 里提到它们的那一句，然后全部重跑。
  轨迹统计显示这个入口两次运行中从未被用过——64 个 session 全都读了三份 `notes.md`，
  没有一个打开过示范合同——所以没有结果依赖于它；但对比的成立不应该建立在"模型主动
  放弃了给它的优势"之上。

- **`claude-opus-5` 是别名而非带日期的快照**，API 返回的即该别名，无法直接锁定。
  **部分修复：**记录可观测项——API 每次调用返回的 `model` 字段、CLI 每个 session 报告的
  `model_usage` 键、manifest 的起止时间。若日后暴露带日期的 id，在 `lib.MODEL` 中锁定。

## 二、review 中两条不成立的判断

- **"memory 位置按 git 仓库确定，仓库下所有工作区共用一份 memory。"** 实际按工作目录
  的绝对路径确定：64 个工作区在 `~/.claude/projects/` 下生成 64 个独立目录，其中没有
  任何一个含 `memory/`。工作区结构无需改动。

- **"轨迹中出现一次 `Bash` 与一次 `Edit` 调用，说明实际代码与提交代码不一致，或工具
  限制失效。"** 两次调用均返回错误：*"No such tool available: Bash. Bash exists but is
  not enabled in this context."* 被拒绝恰恰说明限制生效，而不是失效。仍加入
  `disallowed_tools`，因为"未列出"与"被明确拒绝"是两种不同的说法，只有后者可从外部
  检查。本次运行 64 个 session 全部 727 次工具调用的统计为
  `Read 445 / Write 165 / Glob 92 / Grep 25`，没有别的。

## 三、未解决的问题

- **每个条件仅运行一次。** 两路均未设 temperature 与 seed，API 也不提供确定性采样，
  重跑不会得到相同数字。没有做重复实验（成本原因），因此每个指标**跑与跑之间的波动
  没有量化**。

  有一个附带的观测可以给出量级：agent 一路被完整执行过两次，两次的 `prompt_sha256`、
  `input_sha256`、模型、effort、轮数上限与镜像均一致；两次之间在全语料上 ROC-AUC 相差
  约 0.02，召回率@0.5 相差约 0.10。这是一次观测而非方差估计，但它意味着几个百分点以内
  的 ROC-AUC 差异不能单独拿来解读。哪些结论经得起这个尺度、哪些经不起，见
  [REPORT.md](REPORT.md) 第 6 节。这仍是唯一会影响结果解读方式的遗留项。

- **`claude-opus-5` 是别名。** 见第一节：能观测到的都记录了，但没有带日期的快照可锁。

## 四、记录了什么、记在哪

每次运行留下两类文件，都已提交。

**`run_manifest_<时间戳>.json`**——每次启动一份，首个 session 前写一次，结束时再写一次：

| review 的要求 | 字段 |
|---|---|
| SDK / CLI 版本 | `packages`；`cli_version`、`cli_path`（实际运行的内置二进制）、`cli_on_path`（`claude --version` 会报告的那个） |
| 环境变量白名单 | `env_set`、`env_removed` |
| prompt / 输入哈希 | `prompt_sha256`、`input_sha256`；system prompt 以 `sha256:` 摘要形式存放，不存原文 |
| 模型、effort | `model`（请求的）、`models_seen`（实际计费的）、`effort`、`max_turns` |
| 运行顺序 | `contract_order`、`seed`、`examples` |
| 机器 | `python`、`platform`、`git_commit`、`git_dirty`、`billing` |
| 隔离 | `options` 原文——`setting_sources`、`skills`、`strict_mcp_config`、`disallowed_tools`、`hooks`、`cwd` |
| 容器 | image tag、image id，以及 Dockerfile、entrypoint、`isolation.py` 的 SHA-256 |

**`<cid>.json`**——每份合同一份：模型、effort、`models_seen`、session id、轮数、token
用量、`container_rc`、镜像、`path_denials`，以及 `env_removed`——清扫程序自己给出的
"这里没有东西可删"的记录。旁边还有 `<cid>.trajectory.jsonl` 与 `<cid>.container.log`。

**指令加载清单**是唯一一条我们用别的方式满足的要求，这个差别应当讲清楚而不是含糊过去：
CLI 不输出这样的记录，所以没有一份"实际加载了什么"的正面清单。替代的做法是：镜像里
根本没有可加载的东西——没有 `~/.claude`、没有任何 `CLAUDE.md`、没有 skills、没有托管
配置文件——再由 `preflight.py` 从 session 自己的轨迹里核对它们确实不存在（第五节）。
"构造上不存在 + 反向审计"是在 CLI 不支持的前提下能拿到的最强证据，但它确实弱于
review 要求的那份清单。

## 五、验证方式

`preflight.py` 在剩余合同中选最小的一份，以与正式实验完全相同的选项真实运行一个
session，随后审计其轨迹，任一项不通过即以非零码退出。检查项：

- 轨迹中无 `MEMORY.md`、`CLAUDE.md`、skills 列表、`mcp__*` 工具；
- `<system-reminder>` 仅作为 CLI 包裹工具返回结果出现，无独立指令块；
- 计费模型有且仅有一个，且为指定模型；
- 所有记录的 CLI 版本一致，且等于 SDK 解析出的版本；
- 未使用四个工具以外的工具，此类尝试均被拒绝；
- 无工作区外路径被实际读取（被拒绝的尝试计为通过并记录）；
- 存在覆盖该合同的 manifest，其隔离选项为设定值，且环境开关均实际存在于该 session
  自己的环境中；
- session 跑在预期的镜像里、工作目录只有 `/work`，且容器内需要清扫的环境变量为 0 个
  ——这是在开发机上跑不出来的数字。

该检查早期两次运行失败：一次因 manifest 记录的 CLI 未参与运行，一次因
`DISABLE_NON_ESSENTIAL_MODEL_CALLS` 不是有效变量名。两者均属选项看似生效、实际无效，
仅靠阅读代码无法发现。当前 harness 14 项检查全部通过。

```sh
python src/experiments/preflight.py                  # 运行一个 session 并审计
python src/experiments/preflight.py --audit <cid>    # 审计已运行的 session
```

## 六、复现步骤

```sh
pip install -r requirements.txt        # 宿主机侧：够用来构建镜像并驱动实验
docker build -f docker/Dockerfile -t contract-risk-judge:0.2.139 .
claude setup-token                     # 手动跑一次，token 写进 .env
python src/experiments/preflight.py    # 须输出 PREFLIGHT PASSED
python src/experiments/exp3_agent.py --shuffle --parallel 6
python src/experiments/compare_exp3.py
python src/experiments/plot_exp3_thresholds.py --run agent
```

`--parallel` 只决定同时跑几个容器。每份合同都是独立容器里的独立 session，这个数字
不影响结果。

数据集本身不需要 API key：`step0_corpus.py` 与 `build_dataset.py` 不做模型调用，且
`build_dataset.py` 按记录的字符区间从磁盘重新切分每一行，文字不能逐字复现即拒绝写出。
重跑复现的是流程，模型答案会不同，差异幅度即第三节所述的未解决问题。

每个 session 留下 `output/llm_logs/exp3_agent/<cid>.trajectory.jsonl`，含每次工具调用、
思考块、CLI 版本与工作目录；另有 `<cid>.json` 记录轮数、token 用量、计费模型、被拒绝的
路径以及所用镜像。本文各项说法可据此重新审计，无需重跑。

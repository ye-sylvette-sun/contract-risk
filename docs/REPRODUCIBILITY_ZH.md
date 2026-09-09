# Agent 实验的可复现性（中文）

agent 实验为每份合同开一个全新的对话。仅有这一点是不够的：Claude Code CLI 可以加载
settings 文件、`CLAUDE.md`、memory、skill 和 MCP server，而这些都不会出现在对话里；
它还运行在一个持有答案的文件系统上。本文说明这次运行与什么隔离、每一项保证如何强制
实施、以及如何被核验。

---

## 1. session 与什么隔离

**配置。** SDK 把 `None` 当作「加载全部来源，与 CLI 默认一致」—— 用户级
`~/.claude/settings.json`、项目级 `.claude/settings.json`、本地
`.claude/settings.local.json`，以及工作区上溯路径上的每一个 `CLAUDE.md`。运行时传入的
是显式的空值：

```python
setting_sources=[]          # 任何层级的 settings 文件都不加载
skills=[]                   # 不加载 skill；None 的含义是「不做配置」
strict_mcp_config=True      # 项目、用户、插件的 MCP server 一律不加载
```

**memory 与 `CLAUDE.md` 注入**不受 `setting_sources` 管辖，由环境变量关闭：
`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` 和 `CLAUDE_CODE_DISABLE_PROJECT_CLAUDE_MD=1`。

**环境变量。** `env()` 扫描父进程，**按名移除**每一个可能到达 CLI 的变量 —— API key、
base URL、代理设置、模型覆盖、遥测，全部 —— 而不是只移除已知的少数几个。传进去的，
就是实验选择传进去的。

**非必要流量。** `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`。不设它的话，一次 session
会为后台工作计费第二个更小的模型，那么这次运行就不是单模型的测量。

**自动更新。** `DISABLE_AUTOUPDATER=1`，CLI 不能在运行中途改变版本。

**工具。** 允许四个工具，其余在 `disallowed_tools` 中显式列出 —— 不在白名单里的工具
会被拒绝，而不只是没有被提及。

**文件系统。** session 没有 `Bash`，但 `Read` 和 `Glob` 接受路径。一个 `PreToolUse`
hook 把每个路径参数解析到工作区根目录之下，越界一律拒绝。每次拒绝都会被记录，
`preflight.py` 报告次数。

**机器。** 每个 session 都在自己的容器里运行，镜像按 digest 固定，不按 tag：

```
contract-risk-judge:0.2.139
sha256:b5f50d7dc71f6eae1ce623fbbcad0853e927ce6c564ae44d9ff53015f1bb1fec
```

容器里没有 `~/.claude`、没有 settings、没有 skill、没有托管策略，只有这份合同的工作区
被挂载。`dataset.csv` 和其他合同根本不在它的文件系统上。

**凭据。** `CLAUDE_CODE_OAUTH_TOKEN` 作为单个环境变量传入 —— 订阅凭据，**从不使用
API key** —— 使鉴权进入容器时不带入任何配置。

**评测集。** 持出的不只是 example 合同本身，而是该 example 所在案件提交的**每一份
合同**。同案的其他文书会作为 `context/` 挂载，因此判断一个 example 的同案兄弟，等于把
该 example 自己的被解释条款和法院对它们的原话直接摆在模型面前。

---

## 2. 哪些东西被钉死

全部依赖以 `==` 固定并在运行前提交，包括 `claude-agent-sdk`。CLI 版本通过 SDK 自身的
查找逻辑解析，而不是从 `PATH` 取：SDK 自带一个 CLI 且会优先使用它，所以 `PATH` 上的
版本未必就是实际运行的那个。

`claude-opus-5` 是一个别名而不是带日期的快照，API 在用量记录里返回的也是这个别名。某一天
它背后的确切权重无法从日志中恢复；manifest 记录的是**请求了什么**和**计费了什么**，这是
别名机制允许做到的极限。

---

## 3. 记录了什么、记在哪

```
output/llm_logs/risk_detect_agent/<cid>.json             请求、响应、用量
output/llm_logs/risk_detect_agent/<cid>.trajectory.jsonl session 的每一轮
output/risk_detect_agent/<cid>.json                      返回的判断
output/risk_detect_agent_ws/<cid>/                       挂载时的工作区
output/run_manifest_<timestamp>.json                     镜像 digest、CLI 版本、
                                                         请求与计费的模型、
                                                         各项选项、依赖版本
```

对隔离而言最关键的记录是 trajectory：它显示 session 发起的每一次工具调用，因此「模型只
读到了实验给它的东西」这个主张可以被核验，而不是被假定。

---

## 4. 如何核验

`preflight.py` 端到端跑一份合同，并在值得开始完整运行之前断言结果的十四条性质：

```
TRAJECTORY   trajectory 存在且有记录
CONTAINER    镜像与工作目录是钉死的那一个
MEMORY       没有加载任何 memory 文件
CLAUDE_MD    没有注入任何 CLAUDE.md
SKILLS       没有加载任何 skill
REMINDERS    没有 system reminder 包裹工具结果
CLI          trajectory 中的 CLI 版本就是 SDK 实际启动的那个
MODEL        计费的模型就是请求的模型，且只有这一个
PATHS        有多少路径参数落在工作区之外，其中多少被拒绝
TOOLS        实际使用了哪些工具
MANIFEST     run manifest 已写出
OPTIONS      setting_sources、skills、strict_mcp_config 是隔离所需的值
ENV          清扫了多少变量，以及每个开关是否都已设置
```

这里失败是**不该开跑**的理由，不是一条记下来的警告。

---

## 5. 已知限度

- **模型别名不是快照。** 几个月后的重跑可能在同一个名字下运行不同的权重，而日志中不会
  有任何迹象。
- **运行间方差未测量。** 没有随机种子，没有温度控制，同一 prompt 从未在这个数据集上跑过
  两遍。在做过这件事之前，两个配置无法比较；`compare_risk_detect.py --against` 就是
  为此而存在的。
- **数据集里条款到段落的对应是模型的判断**，不是已核实的事实 —— 见
  [DATASET_ZH.md](DATASET_ZH.md) §5。隔离让**运行**可复现，它不会让**标签**正确。

---

## 6. 复现步骤

```bash
docker build -f docker/Dockerfile -t contract-risk-judge:0.2.139 .
export CLAUDE_CODE_OAUTH_TOKEN=...          # 订阅凭据

python src/experiments/preflight.py         # 十四项必须全部通过
python src/experiments/risk_detect_agent.py --parallel 6

python src/experiments/compare_risk_detect.py
python src/experiments/plot_risk_detect_thresholds.py

python src/experiments/issue_alignment_check.py --parallel 6
python src/experiments/issue_alignment_check.py --control case --out DIR
python src/experiments/plot_issue_alignment_thresholds.py
```

从语料重建数据集：

```bash
python src/step1_inventory.py  --parallel 6
python src/step2_disputes.py   --parallel 6
python src/step3_issue_scope.py --parallel 6
python src/build_dataset.py
```

`step2_disputes.py` 和 `step3_issue_scope.py` 支持续跑：已在产物中的案件会被跳过。
`--cases-file PATH` 只运行文件中列出的 citation（每行一个），并在某个 citation 匹配不到
任何已建清单的案件时直接报错退出。

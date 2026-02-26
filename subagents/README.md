# 四代理协同工作流（无情绪代理）

本目录提供两层能力：
- 编排渲染层：`orchestrate_report.py`（把 3 个子代理 JSON 汇总成最终报告）
- 并发调度层：`scheduler.py`（并发执行、校验、修复、状态持久化、汇总）

## 代理角色
- `总控守门官`
- `宏观行业研判官`
- `空头对抗审计官`
- `技术执行判定官`

## 目录说明
- `agents_manifest.yaml`：4 个代理职责定义
- `contracts.md`：统一数据契约
- `orchestrate_report.py`：总控渲染脚本
- `scheduler.py`：并发调度器（fan-out/fan-in）
- `agent_runner.py`：单代理统一执行接口（mock/command）
- `validator.py`：结构校验与业务门槛校验
- `repair.py`：自动修复策略（缺字段/缺条数/缺分类）
- `state_store.py`：SQLite 运行状态存储
- `runtime.yaml`：调度与执行配置
- `examples/*.json`：最小可运行样例

## 快速运行（并发调度）
```bash
python3 /Users/liuguan1/Documents/github/Trading_assess/subagents/scheduler.py \
  --plan /Users/liuguan1/Documents/github/Trading_assess/subagents/examples/plan.json \
  --config /Users/liuguan1/Documents/github/Trading_assess/subagents/runtime.yaml
```

## 恢复运行（断点续跑）
```bash
python3 /Users/liuguan1/Documents/github/Trading_assess/subagents/scheduler.py \
  --plan /Users/liuguan1/Documents/github/Trading_assess/subagents/examples/plan.json \
  --config /Users/liuguan1/Documents/github/Trading_assess/subagents/runtime.yaml \
  --run-id <已有run_id> \
  --resume
```

## 指定最终报告输出路径
```bash
python3 /Users/liuguan1/Documents/github/Trading_assess/subagents/scheduler.py \
  --plan /Users/liuguan1/Documents/github/Trading_assess/subagents/examples/plan.json \
  --output /Users/liuguan1/Documents/github/Trading_assess/subagents/outputs/report_from_scheduler.md
```

## 仅做汇总渲染（保留旧流程）
```bash
python3 /Users/liuguan1/Documents/github/Trading_assess/subagents/orchestrate_report.py \
  --plan /Users/liuguan1/Documents/github/Trading_assess/subagents/examples/plan.json \
  --macro /Users/liuguan1/Documents/github/Trading_assess/subagents/examples/macro_industry.json \
  --bear /Users/liuguan1/Documents/github/Trading_assess/subagents/examples/bear_case.json \
  --technical /Users/liuguan1/Documents/github/Trading_assess/subagents/examples/technical.json \
  --output /Users/liuguan1/Documents/github/Trading_assess/subagents/outputs/report_example.md
```

## 调度产物
每次调度会生成目录：
- `subagents/runs/<run_id>/inputs/plan.json`
- `subagents/runs/<run_id>/<agent>/output.json`
- `subagents/runs/<run_id>/report.md`
- `subagents/runs/<run_id>/summary.md`
- `subagents/runs/<run_id>/events.jsonl`

全局状态数据库：
- `subagents/runs/state.db`

## 强制校验项
- 宏观风险条数 >= 8
- 宏观利好条数 >= 8
- 利空条数 >= 8
- 利空分类覆盖 `财务/行业景气/政策/公司公告`
- `事实/影响/日期/来源编号` 字段完整
- 日期格式 `YYYY-MM-DD`
- `来源编号` 必须在 `sources` 中可解析
- 技术验证、逻辑证伪、技术失效、出场/砍仓条件不能为空

## 切换真实执行（command mode）
默认 `runtime.yaml` 使用 `mock`。如需调用真实子代理进程：
1. 将 `provider.mode` 或单个 `agents.<name>.mode` 改为 `command`
2. 在 `agents.<name>.command_template` 配置命令模板

模板可用变量：
- `{plan}` 计划文件路径
- `{output}` 该代理输出 JSON 路径
- `{run_dir}` 当前运行目录

示例：
```yaml
agents:
  macro:
    mode: command
    command_template: "python3 /path/to/your_macro_worker.py --plan {plan} --output {output}"
```

## 说明
- `scheduler.py` 负责并发执行 + 校验 + 修复 + 汇总。
- `orchestrate_report.py` 仍是最终报告的统一格式化出口。

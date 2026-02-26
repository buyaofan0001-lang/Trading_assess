# Trading Subagents Runbook

## 目标
将 `AGENTS.md` 的固定流程拆成可复用 subagent，提升两个核心能力：
- 速度：可并行的模块并行执行。
- 质量：每一步有输入输出契约和质检闸门。

## 目录
- `registry.yaml`：subagent 注册表与依赖图。
- `prompts/`：每个 subagent 的执行提示模板。

## 推荐执行顺序
1. `intake_gatekeeper`
2. 并行执行：`macro_radar` + `industry_researcher` + `sentiment_quant`
3. `thesis_decomposer`
4. 并行执行：`bear_challenger` + `technical_mapper`
5. `dual_validator_executor`
6. `archive_clerk`

## 并行提效原则
- 允许并行：只读采集任务（宏观/行业/情绪）。
- 禁止并行：依赖强的收敛任务（双维验证、执行与失效、归档）。
- 当 `Q1/Q2/Q3` 任一闸门未通过时，主控必须回退到对应子任务补证。

## 统一输出约束
- 关键结论必须带：`日期 + 来源链接 + 可访问性校验日期`。
- 术语必须分层：事件层、技术层、因果层。
- 不确定结论统一标注：`未证实/待验证`。
- 任何交易建议只保留条件，不输出硬性买卖指令。

## 快速用法
把某个 subagent 的 prompt 作为独立任务执行，并把产出写回共享上下文；下一 subagent 只消费共享上下文中约定字段，不直接复述上游长文本。

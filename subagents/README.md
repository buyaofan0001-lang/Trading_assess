# 四代理协同工作流（可复用）

本目录提供可直接复用的 4 个中文 subagent：
- `总控守门官`
- `宏观行业研判官`
- `空头对抗审计官`
- `技术执行判定官`

## 目录说明
- `agents_manifest.yaml`：流程顺序、门槛、模板路径。
- `*.md`：各代理可复用提示词模板（可直接给 subagent 使用）。
- `contracts.md`：统一数据契约（含三层结论矩阵）。
- `orchestrate_report.py`：总控脚本，执行门槛校验并渲染最终报告。
- `examples/*.json`：最小可运行样例与调试输入。

## 快速运行
```bash
python3 /Users/liuguan1/Documents/github/Trading_assess/subagents/orchestrate_report.py \
  --plan /Users/liuguan1/Documents/github/Trading_assess/subagents/examples/plan.json \
  --macro /Users/liuguan1/Documents/github/Trading_assess/subagents/examples/macro_industry.json \
  --bear /Users/liuguan1/Documents/github/Trading_assess/subagents/examples/bear_case.json \
  --technical /Users/liuguan1/Documents/github/Trading_assess/subagents/examples/technical.json \
  --output /Users/liuguan1/Documents/github/Trading_assess/subagents/outputs/report_example.md
```

## 强制校验项（由总控脚本执行）
- 宏观风险条数 >= 8。
- 宏观利好条数 >= 8。
- 基本面分析检查固定5维必须齐全（收入结构/利润质量/现金流质量/估值与预期/催化财务映射）。
- 基本面每维必须包含结论、证据来源、日期、统计口径。
- 利空条数 >= 8。
- 利空分类覆盖 `财务/行业景气/政策/公司公告`。
- `事实/影响/日期/来源编号` 字段完整。
- 日期格式必须 `YYYY-MM-DD`。
- `来源编号` 必须能在 `sources` 中找到。
- 技术验证、逻辑证伪、技术失效、出场/砍仓条件不能为空。
- 支撑/阻力兼容两种格式：`[10.5, 10.2]` 或 `[{"价格":10.5,"依据":"前低"}]`。

## 输出
- `report_example.md`：三段式交易验证报告（事实与对抗 / 验证结论 / 执行与失效）。

## 说明
- 当前实现是“编排与校验层”，不负责联网抓取。
- 你可让各 subagent 先产出 JSON，再用总控脚本统一收口。

## 复用建议
1. 复制 `examples/plan.json` 生成本次任务输入。
2. 按 `agents_manifest.yaml` 的顺序派发代理，并将输出写入 `subagents/outputs/`。
3. 执行 `orchestrate_report.py` 自动校验并生成最终报告。

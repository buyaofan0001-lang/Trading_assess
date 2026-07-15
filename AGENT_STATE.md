# AGENT_STATE.md - Shared Project State

Last Updated: 2026-07-15

## Project Snapshot

- Project root: `/Users/liuguan1/Documents/github/Trading_assess`
- Project name: `Trading_assess`
- Purpose: personal trading assessment workspace for transaction logging, review notes, memory, and cross-agent trading collaboration.

## Current Architecture

- Canonical entrypoints: `AGENTS.md` for durable trading-partner policy, `AGENT_STATE.md` for current shared state, `memory.md` for distilled long-term memory.
- Important modules: `对话备份/` stores raw conversation evidence, `复盘/` stores review notes, `交易记录.xlsx` stores objective trade records.
- Execution / runtime assumptions: assistants do not automatically hold every archived chat in context; they must load distilled memory first and retrieve raw archives by topic/date/stock when needed.

## Current Workflow Rules

- Before meaningful trading collaboration, read `AGENTS.md`, `AGENT_STATE.md`, and `memory.md`.
- Before any trading advice, inspect the `Current Holdings Snapshot` below and state uncertainty if the snapshot is stale or incomplete.
- For specific stocks, sectors, dates, trading habits, or "you should remember" requests, search `对话备份/`, `复盘/`, and `memory.md` before answering.
- After each conversation backup, distill durable user preferences, trading-rule upgrades, recurring execution errors, and research-priority changes into `memory.md` when applicable.
- After user-provided holdings/trade screenshots or any update to `交易记录.xlsx`, update the holdings snapshot here.
- Formal outputs must follow `AGENTS.md`, especially evidence-first trading notes and per-turn conversation backups.
- Important constraints: keep `AGENTS.md` as durable policy and this file as current shared state; do not add duplicate state files.
- External dependencies or upstream sources: user-provided screenshots, `交易记录.xlsx`, `memory.md`, and review folders under this repository.

## Current Holdings Snapshot

- Snapshot status: **provisional, not broker-live**. `交易记录.xlsx` was updated through 2026-07-13 (row 194), but the latest diary is 2026-07-15 and may describe decisions/trades not yet recorded. Exact holdings, costs, cash level, and position weights still require a current broker holdings screenshot.
- Source: `交易记录.xlsx` rows 164-194 plus `日记/2026-7-15.md`. The table's latest non-zero `剩余仓位` implies 生益科技 400 shares and 长电科技 400 shares; this is only a temporary reconstruction.
- Reconciliation warning: 生益 row 189 shows `剩余仓位=400`, while its copied reflection text says “最终清仓离场”; the objective remaining-position field and the narrative conflict. Treat 400 shares as provisional until broker confirmation.

| Code | Name | Shares | Last supporting row | Last recorded action | Confidence |
| --- | --- | ---: | ---: | --- | --- |
| 600183 | 生益科技 | 400（待确认） | 189 | 2026-07-07 卖出 100 @155.90；表内剩余 400，但反思文字写“清仓” | Medium |
| 600584 | 长电科技 | 400（待确认） | 194 | 2026-07-13 09:44 买入 400 @104.13 | Medium |
| 603986 | 兆易创新 | 0（表内已清） | 193 | 2026-07-13 09:37 卖出 100 @577.40 | High |
| 600176 | 中国巨石 | 0（表内已清） | 190 | 2026-07-07 09:52 卖出 300 @62.76 | High |
| 002281 | 光迅科技 | 0（表内已清） | 186 | 2026-07-03 15:00 卖出 200 @217.33 | High |
| 600522 | 中天科技 | 0（表内已清） | 182 | 2026-07-01 14:50 卖出 1000 @54.82 | High |
| 002384 | 东山精密 | 0（表内已清） | 171 | 2026-06-29 10:17 卖出 300 @244.53 | High |
| 000636 | 风华高科 | 0（表内已清） | 173 | 2026-06-29 14:54 卖出 200 @80.04 | High |

- Risk-unit note (07-15): if the reconstructed book is accurate, 生益(CCL/PCB) + 长电(封测) remain one broad semiconductor/AI-hardware risk unit rather than genuine style diversification.
- Execution flag: across the 29 valid trade rows from 2026-06-26 through 2026-07-13 there were 12 buys; 10 occurred before 14:30 and only 2 at/after 14:30. The repeatedly written “尾盘买入” rule is not yet reflected in behavior.
- Current psychological/behavioral flag: the 07-15 diary connects trading-for-living pressure, fixed rent/job uncertainty, urgency to recover losses, family recognition, and compulsive quote-checking with FOMO and repeated rule violations. Until the user is calmer and holdings are confirmed, default to risk containment and a no-new-position/cooling-off bias rather than searching for the next trade.

## Current Baselines

- Main command(s): inspect/edit spreadsheet artifacts through the bundled spreadsheet runtime when modifying `.xlsx` files.
- Latest trusted result(s): `交易记录.xlsx` contains 194 rows (header + 193 records) and is updated through row 194 / 2026-07-13. Latest diary is `日记/2026-7-15.md`.
- Known caveats: 2026-06-03 `生益科技` remains as the pre-existing aggregated row because the screenshot only showed a partial lower row, not enough to safely split every trade.

## Open Items

- Next memory task: continue consolidating July records after a broker holdings screenshot confirms the actual book and July drawdown magnitude.
- Next high-priority work: continue appending future broker trade screenshots into `交易记录.xlsx` without inventing buy/sell reasons.
- Known risks: screenshot-cropped rows can hide time or amount; do not split aggregated historical rows unless every objective field is visible or otherwise provided.
- Pending migration or cleanup: none.

## Collaboration Rules

- Read this file before substantial work.
- Update this file after major workflow or result changes.
- Keep it current and concise; remove stale history instead of appending long logs.

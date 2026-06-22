# AGENT_STATE.md - Shared Project State

Last Updated: 2026-06-19

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

- Snapshot status: provisional, not broker-live.
- Last updated: 2026-06-19 ~14:30 Asia/Shanghai.
- Source: user-provided 兴业证券 历史成交 screenshots (taken 2026-06-19 14:18, range 2026-06-12~06-19), 13 new fills (6-15/6-17/6-18) written into `交易记录.xlsx` rows 148-160. Current shares derived from last trusted broker 剩余 + applied trades, NOT raw cumulative sums (history has dirty/aggregated rows).
- Live market check 2026-06-19 (TradingView): 亨通 600487 = 111.01 (日高116.66); 生益 600183 = 183.87 (日高187.35); 东山 002384 = 273.00 (日高277.6).
- Higher-priority source needed: a current **holdings (持仓)** screenshot, not 成交 history — especially to resolve 东山精密 (see caveat).

| Code | Name | Shares | Last supporting row | Last recorded action | Confidence |
| --- | --- | ---: | ---: | --- | --- |
| 600487 | 亨通光电 | 0（已清） | 156 | 2026-06-17 09:40:38 卖出 -400 @107.12，本轮(5-27建仓)全平，毛盈约+20,592(+10.3%) | High |
| 600183 | 生益科技 | 700 | 159 | 2026-06-18 14:56:16 卖出 -300 @183.70 | Medium |
| 002384 | 东山精密 | 600 | 160 | 2026-06-18 14:56:25 买入 200 @270.90 | High |

- 东山精密 reconciliation RESOLVED (2026-06-19): user confirmed the May position was fully cleared on 5-27 @215.62. Root cause was a data error — row 130 was mis-recorded as 买入 400; corrected to 卖出 -400 (clearance). May round: bought 400 (rows 127/129), sold 400 (row 130) → flat; gross P&L ≈ +2,230 (+2.65%). Current 东山 = 600 (June buys only), now reconciles in raw cumulative sum too.
- 亨通光电 fully exited: 6-09 holding 1800 sold down to 0 via rows 147(700@97.35)/150(400@97.84)/152(100@97.51)/154(200@98.51)/156(400@107.12). Note: after exit, 亨通 ran to 111-116 on 6-19. The earlier "1100 底仓 hold with failure level at 91.5" plan was NOT kept — position was fully liquidated by 6-17 instead.
- Confirmed cleared earlier: `思源电气` (user confirmed 2026-06-09).
- Dirty-data caveat: 生益 raw cumulative sum runs 500 short of broker 剩余 (aggregated history row) — anchor on last broker 剩余 + new trades, not raw sums. (东山 prior 800-over discrepancy fixed 2026-06-19 via row-130 correction.)
- Analysis implication: 亨通(光通信/海缆) now exited; current book = 生益科技(PCB/覆铜板) + 东山精密(PCB/精密制造，AI硬件链). Both are PCB / AI-hardware risk units — same-theme crowding INCREASED, not diversified. Treat 东山+生益 as one risk unit.

## Current Baselines

- Main command(s): inspect/edit spreadsheet artifacts through the bundled spreadsheet runtime when modifying `.xlsx` files.
- Latest trusted result(s): on 2026-06-09, `交易记录.xlsx` was updated through row 146 with visible objective trades from screenshots dated 2026-06-04, 2026-06-08, and 2026-06-09.
- Known caveats: 2026-06-03 `生益科技` remains as the pre-existing aggregated row because the screenshot only showed a partial lower row, not enough to safely split every trade.

## Open Items

- Next memory task: periodically consolidate archives after 2026-04-26 into `memory.md`, especially new June 2026 trade-record updates and any post-April behavioral lessons.
- Next high-priority work: continue appending future broker trade screenshots into `交易记录.xlsx` without inventing buy/sell reasons.
- Known risks: screenshot-cropped rows can hide time or amount; do not split aggregated historical rows unless every objective field is visible or otherwise provided.
- Pending migration or cleanup: none.

## Collaboration Rules

- Read this file before substantial work.
- Update this file after major workflow or result changes.
- Keep it current and concise; remove stale history instead of appending long logs.

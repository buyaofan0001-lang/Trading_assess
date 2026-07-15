# AGENT_STATE.md - Shared Project State

Last Updated: 2026-07-15

## Project Snapshot

- Project root: `/Users/liuguan1/Documents/github/Trading_assess`
- Project name: `Trading_assess`
- Purpose: personal trading assessment workspace for transaction logging, review notes, memory, and cross-agent trading collaboration.

## Current Architecture

- Canonical entrypoints: `AGENTS.md` for durable trading-partner policy, `AGENT_STATE.md` for current shared state, `memory.md` for distilled long-term memory.
- Important modules: `对话备份/` stores raw conversation evidence, `复盘/` stores review notes, `交易记录.xlsx` stores objective trade records, and `交易生活执行手册.md` is the current daily execution and recovery protocol.
- Global research skills are now coordinated through one entrypoint: `/Users/liuguan1/.codex/skills/investor-council`. It orchestrates `/Users/liuguan1/.codex/skills/serenity-skill`, `/Users/liuguan1/.codex/skills/buffett-investment-research`, `/Users/liuguan1/.codex/skills/buffett-perspective`, `/Users/liuguan1/.codex/skills/munger-perspective`, plus its internal Howard Marks, Peter Lynch, and Li Lu seats. It uses one canonical evidence packet, module handoff contracts, explicit dissent, and veto hierarchy rather than majority voting. Persona output is a framework-derived lens, not the named investor's current view, and cannot override holdings verification, primary evidence, technical invalidation, concentration limits, or the recovery protocol. Codex must be restarted before relying on automatic skill discovery.
- Execution / runtime assumptions: assistants do not automatically hold every archived chat in context; they must load distilled memory first and retrieve raw archives by topic/date/stock when needed.

## Current Workflow Rules

- Before meaningful trading collaboration, read `AGENTS.md`, `AGENT_STATE.md`, and `memory.md`.
- Before any trading advice, inspect the `Current Holdings Snapshot` below and state uncertainty if the snapshot is stale or incomplete.
- For specific stocks, sectors, dates, trading habits, or "you should remember" requests, search `对话备份/`, `复盘/`, and `memory.md` before answering.
- After each conversation backup, distill durable user preferences, trading-rule upgrades, recurring execution errors, and research-priority changes into `memory.md` when applicable.
- After user-provided holdings/trade screenshots or any update to `交易记录.xlsx`, update the holdings snapshot here.
- During the current recovery phase, use `交易生活执行手册.md` as the default schedule and behavioral gate; do not help rationalize early-session buying or averaging down.
- Formal outputs must follow `AGENTS.md`, especially evidence-first trading notes and per-turn conversation backups.
- Important constraints: keep `AGENTS.md` as durable policy and this file as current shared state; do not add duplicate state files.
- External dependencies or upstream sources: user-provided screenshots, `交易记录.xlsx`, `memory.md`, and review folders under this repository.

## Current Holdings Snapshot

- Snapshot status: **trade-screenshot authoritative for the listed transactions, but not a broker holdings snapshot**. The user-provided screenshot shows the latest listed trade as 2026-07-13; exact current cash, position weights, and any trades after that screenshot still need confirmation.
- Higher-priority source: user screenshot `codex-clipboard-a71c8a5e-b39a-4155-9b39-af2b32216233.png`. Based on the screenshot plus reconciled prior rows, the provisional post-07-13 book is 长电科技 400 shares, 生益科技 0, and 588060 科创50ETF 100份. This assumes no later trades after the screenshot.
- Workbook conflict: `交易记录.xlsx` rows 195-198 record four 2026-07-15 长电买入 totaling 500 shares, but those trades do not appear in the screenshot that the user described as all recent trades. Treat rows 195-198 as unverified and do not use them for current holdings until the user confirms.

| Code | Name | Shares | Last supporting row | Last recorded action | Confidence |
| --- | --- | ---: | ---: | --- | --- |
| 600183 | 生益科技 | 0（截图已清） | screenshot + new row needed | 2026-07-13 13:10:43 卖出 400 @136.140，成交额 54,456 | High for screenshot transaction; workbook not yet patched |
| 600584 | 长电科技 | 400（截图口径，待确认后续） | screenshot + 194 | 2026-07-13 09:44:17 买入 400 @104.130；7月1日另买 200 @106.800、7月3日卖700后归零 | Medium |
| 588060 | 科创50ETF | 100份（截图新增） | screenshot only | 2026-07-10 13:51:22 买入 100 @1.377 | Medium |
| 603986 | 兆易创新 | 0（表内已清） | 193 | 2026-07-13 09:37 卖出 100 @577.40 | High |
| 600176 | 中国巨石 | 0（表内已清） | 190 | 2026-07-07 09:52 卖出 300 @62.76 | High |
| 002281 | 光迅科技 | 0（表内已清） | 186 | 2026-07-03 15:00 卖出 200 @217.33 | High |
| 600522 | 中天科技 | 0（表内已清） | 182 | 2026-07-01 14:50 卖出 1000 @54.82 | High |
| 002384 | 东山精密 | 0（表内已清） | 171 | 2026-06-29 10:17 卖出 300 @244.53 | High |
| 000636 | 风华高科 | 0（表内已清） | 173 | 2026-06-29 14:54 卖出 200 @80.04 | High |

- Risk-unit note (screenshot provisional): after the 07-13 生益 sell, the confirmed recent book is less concentrated than the prior ledger implied, but 长电 remains a semiconductor/AI-hardware exposure. The 588060 ETF is a separate index exposure, not proof of diversification.
- Execution/data flag: the screenshot confirms the 07-13 生益 exit and exposes four ledger issues: missing 生益 -400, missing 07-01 长电 +200, missing 07-10 588060 +100, and 中天科技 sell-time mismatch (14:30:42 screenshot vs 14:50:00 workbook). Rows 195-198 (07-15 长电 buys) are currently unverified rather than confirmed trades.
- Current psychological/behavioral flag: the 07-15 diary connects trading-for-living pressure, fixed rent/job uncertainty, urgency to recover losses, family recognition, and compulsive quote-checking with FOMO and repeated rule violations. Until the user is calmer and holdings are confirmed, default to risk containment and a no-new-position/cooling-off bias rather than searching for the next trade.

## Current Baselines

- Main command(s): inspect/edit spreadsheet artifacts through the bundled spreadsheet runtime when modifying `.xlsx` files.
- Latest trusted result(s): the workbook contains 198 rows, but the latest user screenshot provides higher-priority transaction evidence through 2026-07-13 and has not yet been written back into the workbook. `交易生活执行手册.md` remains the default recovery protocol. On 2026-07-15, `investor-council` was upgraded from a standalone five-seat framework into the single orchestrator for all installed investment skills. Its dependency checker found all four upstream skills, passed installed/missing-path tests, and the upgraded skill passed Codex validation.
- 2026-07-15 长电科技总控会审（信息截至收盘）：研究结论为 `Watchlist`，执行结论为 `Execution blocked`。公司先进封装和利润修复有公告级证据，但其为资本密集、强周期 OSAT，2025 年经营现金流 46.52 亿元低于长期资产现金支出 62.98 亿元；2026Q1 同口径仍为负自由现金流。7 月 15 日收盘 92.46 元、跌 9.97%，按 2026H1 业绩预告推算的 TTM PE 仍约 81-89 倍，安全边际不足。恢复期内长电不得继续加仓。
- 长电双情景风险基线：若按截图口径为 400 股、成本 104.13 元，则 92.46 元对应约 -11.2%，原 -8% 硬止损位 95.80 元已失守；若 rows 195-198 最终确认、持仓为 900 股、成本约 99.85 元，则约 -7.4%，-8% 硬止损位约 91.86 元。上述仅用于纪律审计，最终动作仍需当前券商持仓截图确认。
- Known caveats: 2026-06-03 `生益科技` remains as the pre-existing aggregated row because the screenshot only showed a partial lower row, not enough to safely split every trade.

## Open Items

- Next memory task: continue consolidating July records after a broker holdings screenshot confirms the actual book and July drawdown magnitude.
- Next high-priority work: reconcile the four screenshot-vs-workbook differences before any xlsx edit; obtain a current broker holdings screenshot before 2026-07-16 open; then choose the correct branch of the documented 400-share/900-share 长电 risk plan. No further 长电 buy is permitted in either branch.
- Known risks: screenshot-cropped rows can hide time or amount; do not split aggregated historical rows unless every objective field is visible or otherwise provided.
- Pending migration or cleanup: none.

## Collaboration Rules

- Read this file before substantial work.
- Update this file after major workflow or result changes.
- Keep it current and concise; remove stale history instead of appending long logs.

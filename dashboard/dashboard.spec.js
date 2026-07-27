const { test, expect } = require("@playwright/test");
const dashboardUrl = process.env.DASHBOARD_URL || "http://127.0.0.1:8765";

test.setTimeout(120000);

test("dashboard loads real data and primary interactions work", async ({ page }) => {
  const consoleErrors = [];
  page.on("console", message => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });

  await page.goto(dashboardUrl);
  await expect(page.locator(".peer-card")).toHaveCount(2, { timeout: 120000 });
  await expect(page.locator(".intraday-chart")).toHaveCount(2, { timeout: 30000 });
  const intradaySvgCount = await page.locator(".intraday-chart svg").count();
  if (intradaySvgCount === 2) {
    await expect(page.locator(".chart-series.holding polyline")).toHaveCount(2);
    await expect(page.locator(".chart-legend-item")).toHaveCount(10);
  } else {
    await expect(page.locator(".intraday-empty")).toHaveCount(2);
  }
  const jcetCard = page.locator(".peer-card").filter({ hasText: "长电科技" });
  await expect(jcetCard).toHaveCount(1);
  await expect(jcetCard.locator(".overseas-row")).toHaveCount(2);
  await expect(jcetCard.locator(".overseas-divider")).toContainText("不参与A股排名");
  await expect(jcetCard.locator(".overseas-chip")).toHaveCount(2);
  await expect(jcetCard.locator(".overseas-ai-evidence")).toHaveCount(2);
  await expect(jcetCard.locator(".peer-ai-note")).toContainText("AI自动认定");
  await expect(page.locator(".volatility-boundary")).toHaveCount(2);
  await expect(jcetCard.locator(".volatility-boundary")).toContainText("ATR(14)");
  await expect(jcetCard.locator(".volatility-boundary")).toContainText("当前跌幅");
  await expect(jcetCard.locator(".volatility-boundary")).toContainText("盘中最大下探");
  await expect(jcetCard.locator(".peer-ai-evidence")).toHaveCount(4);
  await expect(page.locator("#portfolioSyncStatus")).toContainText("Excel已同步");
  await expect(page.locator("#riskModelCard")).toContainText("模型分数");
  await expect(page.locator("#riskModelCard")).toContainText("校准路径风险率");
  await expect(page.locator("#riskModelCard")).toContainText("自动仓位动作不可用");
  await expect(page.locator("#riskModelCard")).toContainText("research_only");
  await expect(page.locator("#riskModelSyncStatus")).toContainText("自动同步");
  await expect(page.getByRole("button", { name: "立即同步并重跑" })).toBeEnabled();
  expect(await page.locator(".risk-factor-row").count()).toBeGreaterThanOrEqual(2);
  await expect(page.locator("#macroFrameworkBody h2")).toContainText("宏观视角");
  await expect(page.locator("#macroFrameworkBody")).toContainText("BAMLH0A0HYM2");
  await expect(page.locator("#macroFrameworkFilename")).toContainText("分析框架.md");
  await expect(page.locator("#macroFrameworkSyncStatus")).toContainText("草案已同步");
  await expect(page.locator('a[href="#overnight"]')).toHaveCount(0);
  await expect(page.locator("#overnight")).toHaveCount(0);
  await expect(page.locator(".nav-link")).toHaveCount(8);
  await expect(page.locator("#permissionCard")).toHaveClass(/red/);
  await expect(page.locator("#gate-title")).toContainText("停止主动买入");
  await expect(page.locator("#freshness")).toContainText("数据已更新");
  await expect(page.locator("#premarketReportBody h1")).toContainText("A股盘前", { timeout: 30000 });
  expect(await page.locator("#premarketList .report-entry").count()).toBeGreaterThanOrEqual(1);
  await expect(page.locator("#premarketSyncStatus")).toContainText("60秒监控");
  await expect(page.locator("#closeReportBody h1")).toContainText("A股盘后复盘", { timeout: 30000 });
  expect(await page.locator("#closeList .report-entry").count()).toBeGreaterThanOrEqual(5);
  await expect(page.locator("#closeReportBody .report-table-scroll").first()).toBeVisible();

  await expect(page.locator('[data-timeframe="5d"]')).toHaveAttribute("data-ready", "true", { timeout: 120000 });
  const switchStarted = Date.now();
  await page.getByRole("button", { name: "5日" }).click();
  await expect(page.locator('[data-timeframe="5d"]')).toHaveClass(/active/);
  await expect(page.locator(".peer-table thead").first()).toContainText("5D收益");
  expect(Date.now() - switchStarted).toBeLessThan(750);

  const refreshResponse = page.waitForResponse(response => response.url().includes("/api/dashboard") && response.ok());
  await page.getByRole("button", { name: "刷新数据" }).click();
  await refreshResponse;

  const knownJournal = page.locator('[data-journal-date="2026-07-15"]');
  await expect(knownJournal).toHaveCount(1);
  expect(await page.locator(".journal-entry").count()).toBeGreaterThanOrEqual(6);
  await knownJournal.click();
  await expect(page.locator("#journalFilename")).toContainText("2026-7-15.md");
  await expect(page.locator("#journalContent")).not.toHaveValue("");
  expect(consoleErrors).toEqual([]);
});

test("journal editor marks changes and reports a successful save", async ({ page }) => {
  await page.route("**/api/journal", async route => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    const body = route.request().postDataJSON();
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        date: body.date,
        filename: `${body.date}.md`,
        modified_at: "2026-07-16T15:30:00+08:00",
        chars: body.content.length,
        excerpt: body.content,
        content: body.content,
        exists: true,
        created: true,
      }),
    });
  });
  await page.goto(`${dashboardUrl}/#journal`);
  await expect(page.locator('[data-journal-date="2026-07-15"]')).toHaveCount(1);
  expect(await page.locator(".journal-entry").count()).toBeGreaterThanOrEqual(6);
  await page.locator("#journalDate").fill("2099-12-31");
  await page.locator("#journalDate").dispatchEvent("change");
  await expect(page.locator("#journalFilename")).toContainText("2099-12-31.md");
  await page.locator("#journalContent").fill("用于验证保存反馈，不写入真实日记目录。");
  await expect(page.locator("#journalStatus")).toContainText("未保存");
  await expect(page.locator("#journalSave")).toBeEnabled();
  await page.locator("#journalSave").click();
  await expect(page.locator("#journalStatus")).toContainText("已保存");
  await expect(page.locator('[data-journal-date="2099-12-31"]')).toHaveCount(1);
});

test("market-hours timer automatically requests a fresh dashboard", async ({ page }) => {
  await page.clock.install({ time: new Date("2026-07-16T05:00:00Z") });
  await page.goto(dashboardUrl);
  await expect(page.locator(".peer-card")).toHaveCount(2, { timeout: 120000 });
  const automaticResponse = page.waitForResponse(response => response.url().includes("/api/dashboard") && response.ok());
  await page.clock.fastForward(31_000);
  await automaticResponse;
  await expect(page.locator("#freshness")).toContainText("自动刷新");
});

test("daily report directories are polled outside the market refresh cycle", async ({ page }) => {
  await page.clock.install({ time: new Date("2026-07-16T09:30:00Z") });
  await page.goto(`${dashboardUrl}/#premarket`);
  await expect(page.locator("#premarketReportBody h1")).toContainText("A股盘前", { timeout: 30000 });
  const reportResponse = page.waitForResponse(response => response.url().endsWith("/api/reports") && response.ok());
  await page.clock.fastForward(61_000);
  await reportResponse;
});

test("risk model result is polled independently", async ({ page }) => {
  await page.clock.install({ time: new Date("2026-07-16T09:30:00Z") });
  await page.goto(`${dashboardUrl}/#risk-model`);
  await expect(page.locator("#riskModelCard")).toContainText("模型分数");
  const riskResponse = page.waitForResponse(response => response.url().endsWith("/api/risk-model") && response.ok());
  await page.clock.fastForward(61_000);
  await riskResponse;
});

test("macro framework source is polled independently", async ({ page }) => {
  await page.clock.install({ time: new Date("2026-07-16T09:30:00Z") });
  await page.goto(`${dashboardUrl}/#macro-framework`);
  await expect(page.locator("#macroFrameworkBody h2")).toContainText("宏观视角");
  const frameworkResponse = page.waitForResponse(response => response.url().endsWith("/api/macro-framework") && response.ok());
  await page.clock.fastForward(61_000);
  await frameworkResponse;
});

test("macro monitor renders recent anomalies, dates and source-backed metrics", async ({ page }) => {
  const history = Array.from({ length: 30 }, (_, index) => ({
    date: `2026-06-${String(index + 1).padStart(2, "0")}`,
    value: 270 + index * 0.3,
  }));
  const metric = (id, name, latest, unit, changeUnit, source) => ({
    id,
    name,
    latest,
    date: "2026-07-24",
    change_1d: 1,
    change_5d: 6,
    unit,
    change_unit: changeUnit,
    source,
    source_url: "https://example.com/source",
    components: {},
    age_days: 1,
    stale: false,
    history,
  });
  await page.route("**/api/macro-signals*", async route => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        generated_at: "2026-07-28T10:00:00+08:00",
        partial: false,
        errors: [],
        anomaly_rule: { method: "相对前60个观测的稳健Z分数，绝对值≥2.0才列为异动" },
        summary: {
          headline: "信用风险偏好转弱；长端利率上行",
          credit: "OAS扩大且JNK下跌。",
          rates: "美国10年期收益率上行。",
          liquidity: "中美融资管道未见持续拥堵。",
          boundary: "机械统计异动只描述偏离常态的幅度，不直接生成买卖许可。",
        },
        anomalies: [{
          date: "2026-07-23",
          window: "1D",
          z: 3.04,
          change: 9,
          unit: "bp",
          name: "美国高收益债 OAS",
          severity: "extreme",
          meaning: "信用利差突然扩张，风险偏好转弱。",
        }],
        metrics: [
          metric("oas", "美国高收益债 OAS", 279, "bp", "bp", "FRED · ICE BofA"),
          metric("jnk", "JNK 高收益债 ETF", 95.5, "USD", "%", "Yahoo Finance · JNK"),
          metric("ust10y", "美国10年期国债收益率", 4.71, "%", "bp", "FRED · DGS10"),
          metric("cn_repo_spread", "FR007−FDR007 非银融资溢价", 1.51, "bp", "bp", "中国货币网"),
          metric("usd_funding_spread", "SOFR−IORB 美元融资利差", -1, "bp", "bp", "FRED · SOFR / IORB"),
        ],
      }),
    });
  });
  await page.goto(`${dashboardUrl}/#macro-framework`);
  await expect(page.locator("#macroSignalHeadline")).toContainText("信用风险偏好转弱");
  await expect(page.locator(".macro-anomaly-card")).toHaveCount(1);
  await expect(page.locator(".macro-anomaly-card")).toContainText("2026-07-23");
  await expect(page.locator(".macro-anomaly-card")).toContainText("3.04σ");
  await expect(page.locator("#macroMetricGrid .macro-metric-card")).toHaveCount(5);
  await expect(page.locator("#macroMetricGrid")).toContainText("FRED · DGS10");
  await expect(page.locator("#macroSignalBoundary")).toContainText("不直接生成买卖许可");
});

test("risk model can request a non-blocking manual refresh", async ({ page }) => {
  await page.route("**/api/risk-model/refresh", async route => {
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({ started: true, status: "checking", running: true }),
    });
  });
  await page.goto(`${dashboardUrl}/#risk-model`);
  await page.getByRole("button", { name: "立即同步并重跑" }).click();
  await expect(page.locator("#toast")).toContainText("已开始同步最新数据并重跑冻结V8");
});

test("missing due report is clearly marked instead of looking current", async ({ page }) => {
  await page.route("**/api/reports", async route => {
    const response = await route.fetch();
    const payload = await response.json();
    payload.reports.premarket.health = {
      today: "2026-07-17",
      due: true,
      stale: true,
      status: "missing",
      message: "2026-07-17 报告尚未生成；当前展示 2026-07-15。若今天是交易日，请检查每日任务。",
    };
    await route.fulfill({ response, json: payload });
  });
  await page.goto(`${dashboardUrl}/#premarket`);
  await expect(page.locator("#premarketSyncStatus")).toContainText("今日未生成");
  await expect(page.locator("#premarketHealthAlert")).toContainText("当前展示 2026-07-15");
  await expect(page.locator("#premarketHealthAlert")).toBeVisible();
});

test("mobile layout does not overflow horizontally", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(dashboardUrl);
  await expect(page.locator(".peer-card")).toHaveCount(2, { timeout: 120000 });
  await expect(page.locator("#riskModelCard .risk-score-ring")).toHaveCount(1);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
  expect(overflow).toBe(false);
});

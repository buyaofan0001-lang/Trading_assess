const { test, expect } = require("@playwright/test");

test.setTimeout(120000);

test("dashboard loads real data and primary interactions work", async ({ page }) => {
  const consoleErrors = [];
  page.on("console", message => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });

  await page.goto("http://127.0.0.1:8765");
  await expect(page.locator(".peer-card")).toHaveCount(2, { timeout: 120000 });
  await expect(page.locator(".intraday-chart svg")).toHaveCount(2, { timeout: 30000 });
  await expect(page.locator(".chart-series.holding polyline")).toHaveCount(2);
  await expect(page.locator(".chart-legend-item")).toHaveCount(9);
  const jcetCard = page.locator(".peer-card").filter({ hasText: "长电科技" });
  await expect(jcetCard).toHaveCount(1);
  await expect(jcetCard.locator(".overseas-row")).toHaveCount(2);
  await expect(jcetCard.locator(".overseas-divider")).toContainText("不参与A股排名");
  await expect(jcetCard.locator(".overseas-chip")).toHaveCount(2);
  await expect(page.locator("#permissionCard")).toHaveClass(/red/);
  await expect(page.locator("#gate-title")).toContainText("停止主动买入");
  await expect(page.locator("#freshness")).toContainText("自动刷新");

  await expect(page.locator('[data-timeframe="5d"]')).toHaveAttribute("data-ready", "true", { timeout: 120000 });
  const switchStarted = Date.now();
  await page.getByRole("button", { name: "5日" }).click();
  await expect(page.locator('[data-timeframe="5d"]')).toHaveClass(/active/);
  await expect(page.locator(".peer-table thead").first()).toContainText("5D收益");
  expect(Date.now() - switchStarted).toBeLessThan(750);

  const refreshResponse = page.waitForResponse(response => response.url().includes("/api/dashboard") && response.ok());
  await page.getByRole("button", { name: "刷新数据" }).click();
  await refreshResponse;

  await page.locator('[data-check="0"]').check();
  await expect(page.locator("#checkProgress")).toContainText("1 / 5");
  await expect(page.locator(".journal-entry")).toHaveCount(6);
  await page.locator('[data-journal-date="2026-07-15"]').click();
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
  await page.goto("http://127.0.0.1:8765/#journal");
  await expect(page.locator(".journal-entry")).toHaveCount(6);
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
  await page.goto("http://127.0.0.1:8765");
  await expect(page.locator(".peer-card")).toHaveCount(2, { timeout: 120000 });
  const automaticResponse = page.waitForResponse(response => response.url().includes("/api/dashboard") && response.ok());
  await page.clock.fastForward(31_000);
  await automaticResponse;
  await expect(page.locator("#freshness")).toContainText("自动刷新");
});

test("mobile layout does not overflow horizontally", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("http://127.0.0.1:8765");
  await expect(page.locator(".peer-card")).toHaveCount(2, { timeout: 120000 });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
  expect(overflow).toBe(false);
});

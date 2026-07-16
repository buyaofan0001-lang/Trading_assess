const { test, expect } = require("@playwright/test");

test.setTimeout(120000);

test("dashboard loads real data and primary interactions work", async ({ page }) => {
  const consoleErrors = [];
  page.on("console", message => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });

  await page.goto("http://127.0.0.1:8765");
  await expect(page.locator(".peer-card")).toHaveCount(2, { timeout: 120000 });
  await expect(page.locator("#permissionCard")).toHaveClass(/red/);
  await expect(page.locator("#gate-title")).toContainText("停止主动买入");

  await page.getByRole("button", { name: "5日" }).click();
  await expect(page.locator('[data-timeframe="5d"]')).toHaveClass(/active/);
  await expect(page.locator(".peer-table thead").first()).toContainText("5D收益", { timeout: 120000 });

  const refreshResponse = page.waitForResponse(response => response.url().includes("/api/dashboard") && response.ok());
  await page.getByRole("button", { name: "刷新数据" }).click();
  await refreshResponse;

  await page.locator('[data-check="0"]').check();
  await expect(page.locator("#checkProgress")).toContainText("1 / 5");
  expect(consoleErrors).toEqual([]);
});

test("mobile layout does not overflow horizontally", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("http://127.0.0.1:8765");
  await expect(page.locator(".peer-card")).toHaveCount(2, { timeout: 120000 });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
  expect(overflow).toBe(false);
});

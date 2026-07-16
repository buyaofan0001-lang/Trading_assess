# 守门台 V1

个人交易控制看板初版。它先回答“今天允许做什么”，再展示持仓相对同行强弱、隔夜美股环境、盘后资金流与恢复期清单。

## 启动

在项目根目录运行：

```bash
python3 dashboard/server.py
```

浏览器打开 <http://127.0.0.1:8765>。

## 数据口径

- A股价格：项目统一入口 `tushare_client.py`，使用单代码 `rt_k` 轮询，缓存 25 秒。
- 5日/20日收益：Tushare 日线，缓存 30 分钟。
- 板块资金流：同花顺口径日频数据，只显示最近可用盘后日期。
- 美股：Yahoo Finance 最近完整交易日，缓存 30 分钟。
- 长电科技仓位冲突未解决前，不计算组合盈亏或仓位影响。
- 同行与海外映射均是 V1 候选名单，需用户确认后锁定。

## 可配置项

编辑 `dashboard/config.json` 可调整持仓、同行候选、美股映射与每日清单。Token 仍由根目录 `tushare_client.py` 统一初始化，不写入看板源码。

## 浏览器验收

首次运行：

```bash
cd dashboard
npm install
npx playwright install chromium
npm test
```

测试覆盖真实数据加载、红灯权限、5日切换、刷新、清单勾选、控制台错误和390px窄屏溢出。

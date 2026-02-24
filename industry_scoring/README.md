# Industry Scoring (Batch)

按行业成分股平均数批量计算主题景气度分数：

`Score = 0.40*F + 0.30*O + 0.20*M + 0.10*V`

## 1. 输入数据（逐股）

CSV 必填列：

- `date`：日期（如 `2026-01-19`）
- `industry`：行业名（如 `存储芯片`）
- `stock_code`：股票代码
- `f_revenue_yoy`：营收同比（%）
- `f_profit_yoy`：净利同比（%）
- `f_ocf_yoy`：经营现金流同比（%）
- `o_contract_liab_yoy`：合同负债同比（%）
- `o_capex_yoy`：资本开支同比（%）
- `o_inventory_yoy`：存货同比（%）
- `m_return_20d`：20日收益率（%）
- `m_is_up`：当日上涨标记（上涨=1，其他=0）
- `m_net_inflow_ratio`：主力净流入占成交额（%）
- `v_pe_percentile`：PE历史分位（0-100）

## 2. 计算逻辑

1. 先按 `date + industry` 聚合，得到行业成分股平均数。  
2. 每个日期内，对各行业指标做 `5%-95%` 缩尾 + 横截面 z-score。  
3. 因子构建：
- `F`: `f_revenue_yoy(0.4) + f_profit_yoy(0.4) + f_ocf_yoy(0.2)`
- `O`: `o_contract_liab_yoy(0.45) + o_capex_yoy(0.35) - o_inventory_yoy(0.2)`
- `M`: `m_return_20d(0.4) + m_breadth_up(0.3) + m_net_inflow_ratio(0.3)`
- `V`: `- v_pe_percentile(1.0)`（反向）
4. 因子分映射：`FactorScore = clip(50 + 15*z, 0, 100)`  
5. 总分：`0.4*F + 0.3*O + 0.2*M + 0.1*V`

## 3. 运行

```bash
python3 /Users/liuguan1/Documents/github/Trading_assess/industry_scoring/calc_industry_scores.py \
  --input /Users/liuguan1/Documents/github/Trading_assess/industry_scoring/sample_stock_metrics.csv \
  --output /Users/liuguan1/Documents/github/Trading_assess/industry_scoring/sample_industry_scores.csv \
  --date 2026-01-19 \
  --pass-threshold 70 \
  --min-components 5
```

## 4. 输出字段

- `F_score/O_score/M_score/V_score`：因子分（0-100）
- `F_z/O_z/M_z/V_z`：因子 z 值
- `industry_prosperity_score`：行业景气总分
- `pass_to_s2`：是否通过 S1（1=通过，0=不通过）

## 5. 实时数据版（AkShare + TuShare）

新增脚本：

- `/Users/liuguan1/Documents/github/Trading_assess/industry_scoring/daily_industry_ranking.py`
- `/Users/liuguan1/Documents/github/Trading_assess/industry_scoring/run_daily_ranking.sh`

### 5.1 用法

先设置 TuShare Token（建议）：

```bash
export TUSHARE_TOKEN="你的token"
```

运行当日行业排行榜（全行业）：

```bash
python3 /Users/liuguan1/Documents/github/Trading_assess/industry_scoring/daily_industry_ranking.py \
  --date "$(date +%F)" \
  --output-dir /Users/liuguan1/Documents/github/Trading_assess/industry_scoring/daily_outputs
```

仅跑指定行业：

```bash
python3 /Users/liuguan1/Documents/github/Trading_assess/industry_scoring/daily_industry_ranking.py \
  --date 2026-02-24 \
  --industries "存储芯片,电网设备,光模块" \
  --output-dir /Users/liuguan1/Documents/github/Trading_assess/industry_scoring/daily_outputs
```

不使用 TuShare（仅 AkShare，F/O 会变成中性）：

```bash
python3 /Users/liuguan1/Documents/github/Trading_assess/industry_scoring/daily_industry_ranking.py \
  --date "$(date +%F)" \
  --disable-tushare
```

### 5.2 输出

- 逐股指标：`daily_outputs/stock_metrics_<YYYY-MM-DD>.csv`
- 行业排行榜：`daily_outputs/industry_scores_<YYYY-MM-DD>.csv`

### 5.3 每日自动运行（cron 示例）

打开定时任务：

```bash
crontab -e
```

加入一条（每个交易日 18:05 运行）：

```bash
5 18 * * 1-5 /Users/liuguan1/Documents/github/Trading_assess/industry_scoring/run_daily_ranking.sh >> /Users/liuguan1/Documents/github/Trading_assess/industry_scoring/daily_outputs/cron.log 2>&1
```

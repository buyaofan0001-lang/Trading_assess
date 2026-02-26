# 四代理数据契约

## 1. EvidenceItem
```json
{
  "id": "S1",
  "title": "来源标题",
  "url": "https://...",
  "level": "一级|二级|三级",
  "publisher": "发布主体",
  "publish_date": "YYYY-MM-DD",
  "access_checked_at": "YYYY-MM-DD",
  "subject_verified": true
}
```

## 2. FactRow
```json
{
  "编号": "R1|P1|B1",
  "事实": "...",
  "影响": "...",
  "日期": "YYYY-MM-DD",
  "来源编号": "S1",
  "类别": "财务|行业景气|政策|公司公告"
}
```

## 3. 宏观行业研判输出
```json
{
  "宏观清单": {
    "风险": ["FactRow x >=8"],
    "利好": ["FactRow x >=8"]
  },
  "行业深研": {
    "政策风向": {"结论": "...", "证据来源": ["S1", "S2"]},
    "行业景气度": {"结论": "...", "证据来源": ["S3"]},
    "未来增值空间": {"结论": "...", "证据来源": ["S4"]}
  },
  "sources": {"S1": "EvidenceItem", "S2": "EvidenceItem"}
}
```

## 4. 空头对抗输出
```json
{
  "空头对抗": {
    "利空": ["FactRow x >=8"]
  },
  "coverage_gaps": [],
  "sources": {"S1": "EvidenceItem"}
}
```

## 5. 技术执行输出
```json
{
  "技术面": {
    "latest_price": 0.0,
    "support_levels": [0.0],
    "resistance_levels": [0.0],
    "fvg_zones": ["..."],
    "ob_zones": ["..."],
    "pattern_and_fail_level": {
      "pattern": "上升三角",
      "fail_level": 0.0,
      "note": "若无明显形态，pattern填空并注明无模式关键位"
    }
  },
  "验证": {
    "技术验证": {"状态": "成立|不成立", "依据": "..."},
    "逻辑证伪条件": ["..."],
    "技术失效条件": ["..."]
  },
  "执行与失效": {
    "出场条件": ["..."],
    "无条件砍仓条件": ["..."]
  }
}
```

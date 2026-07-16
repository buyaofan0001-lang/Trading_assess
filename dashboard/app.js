const LIVE_REFRESH_MS = 30_000;
const POST_CLOSE_REFRESH_MS = 5 * 60_000;
const state = {
  timeframe: "1d",
  loading: false,
  data: null,
  timeframeCache: new Map(),
  timeframeRequests: new Map(),
  intraday: null,
  intradayError: null,
  intradayLoading: false,
  refreshTimer: null,
  countdownTimer: null,
  nextRefreshAt: null,
  lastMeta: null,
  journal: {
    entries: [],
    date: null,
    content: "",
    original: "",
    filename: "",
    exists: false,
    loading: false,
    saving: false,
  },
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function fmtPct(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const number = Number(value) * 100;
  return `${number > 0 ? "+" : ""}${number.toFixed(digits)}%`;
}

function fmtNumber(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return Number(value).toLocaleString("zh-CN", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function tone(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "neutral";
  return Number(value) > 0 ? "positive" : Number(value) < 0 ? "negative" : "neutral";
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
  })[char]);
}

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("show");
  window.setTimeout(() => node.classList.remove("show"), 2200);
}

function renderPermission(data) {
  const permission = data.permission;
  const card = $("#permissionCard");
  card.className = `permission-card ${permission.level}`;
  $("#gate-title").textContent = permission.label;
  $("#permissionReason").textContent = permission.reason;
  $("#permissionLabel").textContent = permission.level === "red" ? "红灯 · 阻断" : "黄灯 · 受限";
  $("#allowedList").innerHTML = permission.allowed.map(item => `<li>${escapeHtml(item)}</li>`).join("");
  $("#blockedList").innerHTML = permission.blocked.map(item => `<li>${escapeHtml(item)}</li>`).join("");
  $("#nextDecision").textContent = permission.next_decision;
  const positions = (data.holdings_status.positions || []).map(position => `
    <div class="${tone(position.pnl_pct)}">${escapeHtml(position.name)} ${escapeHtml(position.shares_display)} · 成本 ${fmtNumber(position.avg_cost, 3)} · 浮动 ${fmtPct(position.pnl_pct)} / ${fmtNumber(position.pnl_amount, 0)}元</div>`).join("");
  $("#holdingTruth").innerHTML = `
    <div>${escapeHtml(data.holdings_status.confirmed)}</div>
    ${data.holdings_status.unresolved ? `<div class="danger">${escapeHtml(data.holdings_status.unresolved)}</div>` : ""}
    ${positions}`;
}

function memberRow(row, isHolding) {
  return `<tr class="${isHolding ? "holding-row" : ""}">
    <td><span class="member-name">${escapeHtml(row.name)}</span><span class="code"> ${escapeHtml(row.ts_code)}</span>${isHolding ? '<span class="holding-chip">持仓</span>' : ""}</td>
    <td>${fmtNumber(row.price)}</td>
    <td class="${tone(row.return)}">${fmtPct(row.return)}</td>
    <td class="${tone(row.vs_vwap)}">${fmtPct(row.vs_vwap)}</td>
    <td>${fmtPct(row.turnover_intensity)}</td>
  </tr>`;
}

function overseasRows(group) {
  const mapping = state.data?.us?.mapped_peers?.find(item => item.holding === group.holding.name);
  if (!mapping?.peers?.length) return "";
  const marketDate = state.data?.us?.market_date || "日期待确认";
  return `<tr class="overseas-divider">
    <td colspan="5"><span>隔夜海外同行</span><small>纽约市场日 ${escapeHtml(marketDate)} · 仅作跨市场参照，不参与A股排名</small></td>
  </tr>${mapping.peers.map(row => `<tr class="overseas-row">
    <td><span class="member-name">${escapeHtml(row.name)}</span><span class="code"> ${escapeHtml(row.ticker)}</span><span class="overseas-chip">隔夜</span></td>
    <td>${fmtNumber(row.close)}</td>
    <td class="${tone(row.return)}">${fmtPct(row.return)}</td>
    <td class="neutral">—</td>
    <td class="neutral">—</td>
  </tr>`).join("")}`;
}

const CHART_COLORS = ["#d6a65f", "#7eb0ad", "#85b879", "#c58d78", "#a49cc4"];

function sessionPosition(time) {
  const [hour, minute] = String(time).split(":").map(Number);
  const clock = hour * 60 + minute;
  if (!Number.isFinite(clock)) return null;
  if (clock >= 9 * 60 + 30 && clock <= 11 * 60 + 30) return clock - (9 * 60 + 30);
  if (clock >= 13 * 60 && clock <= 15 * 60) return 135 + clock - 13 * 60;
  return null;
}

function axisPct(value) {
  const pct = Number(value) * 100;
  return `${pct > 0 ? "+" : ""}${pct.toFixed(Math.abs(pct) >= 10 ? 1 : 2)}%`;
}

function renderIntradayChart(group) {
  const payload = state.intraday?.groups?.find(item => item.holding_ts_code === group.holding.ts_code);
  const fallbackCount = (payload?.series || []).filter(item => item.source?.startsWith("Yahoo")).length;
  const sourceBase = state.intraday?.meta?.source || "正在连接当日分钟行情";
  const source = fallbackCount ? `${sourceBase} · ${fallbackCount}只快速回退` : sourceBase;
  const tradeDate = state.intraday?.meta?.trade_date || "";
  const series = payload?.series || [];
  const members = new Map([group.holding, ...group.peers].map(member => [member.ts_code, member]));
  const generatedTime = state.intraday?.meta?.generated_at
    ? new Date(state.intraday.meta.generated_at).toLocaleTimeString("zh-CN", { timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
    : null;
  const usable = series.map((item, index) => {
    const member = members.get(item.ts_code) || {};
    const preClose = Number(member.pre_close);
    const points = (item.points || []).map(point => ({
      ...point,
      position: sessionPosition(point.time),
      value: preClose > 0 ? Number(point.price) / preClose - 1 : Number.NaN,
    })).filter(point => point.position !== null && Number.isFinite(point.value));
    const currentPrice = Number(member.price);
    if (member.source?.includes("rt_k") && generatedTime && Number.isFinite(currentPrice) && preClose > 0) {
      const lastTime = points.at(-1)?.time;
      const position = sessionPosition(generatedTime);
      if (position !== null && (!lastTime || lastTime < generatedTime)) {
        points.push({ time: generatedTime, price: currentPrice, position, value: currentPrice / preClose - 1 });
      }
    }
    return {
      ...item,
      color: CHART_COLORS[index % CHART_COLORS.length],
      points,
      latest_time: points.at(-1)?.time || item.latest_time,
    };
  }).filter(item => item.points.length);

  if (!usable.length) {
    const message = state.intradayError
      ? `分钟行情暂不可用：${state.intradayError}`
      : state.intradayLoading ? "正在加载当日分钟线…" : "今天尚无可用分钟行情";
    return `<figure class="intraday-chart intraday-empty" aria-label="${escapeHtml(group.holding.name)}当日走势">
      <figcaption><div><span class="chart-title">当日价格变化</span><span class="chart-subtitle">相对昨收</span></div><span class="chart-source">${escapeHtml(source)}</span></figcaption>
      <div class="intraday-empty-copy">${escapeHtml(message)}</div>
    </figure>`;
  }

  const width = 1120;
  const height = 258;
  const pad = { left: 57, right: 22, top: 18, bottom: 36 };
  const innerWidth = width - pad.left - pad.right;
  const innerHeight = height - pad.top - pad.bottom;
  const values = usable.flatMap(item => item.points.map(point => point.value));
  const maxAbs = Math.max(0.01, ...values.map(value => Math.abs(value))) * 1.08;
  const x = position => pad.left + position / 255 * innerWidth;
  const y = value => pad.top + (maxAbs - value) / (maxAbs * 2) * innerHeight;
  const yTicks = [-maxAbs, -maxAbs / 2, 0, maxAbs / 2, maxAbs];
  const xTicks = [
    [0, "09:30"], [60, "10:30"], [120, "11:30"],
    [135, "13:00"], [195, "14:00"], [255, "15:00"],
  ];
  const grid = yTicks.map(value => `<g>
    <line x1="${pad.left}" y1="${y(value).toFixed(2)}" x2="${width - pad.right}" y2="${y(value).toFixed(2)}" class="chart-grid${Math.abs(value) < 1e-10 ? " zero" : ""}" />
    <text x="${pad.left - 10}" y="${(y(value) + 3).toFixed(2)}" text-anchor="end" class="chart-axis-label">${axisPct(value)}</text>
  </g>`).join("");
  const axes = xTicks.map(([position, label]) => `<g>
    <line x1="${x(position).toFixed(2)}" y1="${pad.top}" x2="${x(position).toFixed(2)}" y2="${height - pad.bottom}" class="chart-grid vertical" />
    <text x="${x(position).toFixed(2)}" y="${height - 13}" text-anchor="middle" class="chart-axis-label">${label}</text>
  </g>`).join("");
  const lines = usable.map(item => {
    const holding = item.ts_code === group.holding.ts_code;
    const points = item.points.map(point => `${x(point.position).toFixed(2)},${y(point.value).toFixed(2)}`).join(" ");
    const latest = item.points[item.points.length - 1];
    return `<g class="chart-series${holding ? " holding" : ""}">
      <polyline points="${points}" fill="none" stroke="${item.color}" stroke-width="${holding ? 3.2 : 1.8}" vector-effect="non-scaling-stroke" />
      <circle cx="${x(latest.position).toFixed(2)}" cy="${y(latest.value).toFixed(2)}" r="${holding ? 4 : 3}" fill="${item.color}"><title>${escapeHtml(item.name)} ${escapeHtml(latest.time)} ${axisPct(latest.value)}</title></circle>
    </g>`;
  }).join("");
  const legend = usable.map(item => {
    const latest = item.points[item.points.length - 1];
    const holding = item.ts_code === group.holding.ts_code;
    return `<span class="chart-legend-item${holding ? " holding" : ""}" title="${escapeHtml(item.source || "")}">
      <i style="--series-color:${item.color}"></i><span>${escapeHtml(item.name)}</span><b class="${tone(latest.value)}">${fmtPct(latest.value)}</b>
    </span>`;
  }).join("");
  const missing = payload?.missing?.length ? ` · 缺失 ${payload.missing.join("、")}` : "";
  const latestTimes = usable.map(item => item.latest_time).filter(Boolean).sort();
  const latestTime = latestTimes.length
    ? latestTimes[0] === latestTimes.at(-1) ? latestTimes[0] : `${latestTimes[0]}–${latestTimes.at(-1)}`
    : "—";

  return `<figure class="intraday-chart" aria-label="${escapeHtml(group.holding.name)}及同行当日相对昨收走势">
    <figcaption>
      <div><span class="chart-title">当日价格变化</span><span class="chart-subtitle">相对昨收 · 持仓高亮</span></div>
      <span class="chart-source">${escapeHtml(tradeDate)} ${escapeHtml(latestTime)} · ${escapeHtml(source)}${escapeHtml(missing)}</span>
    </figcaption>
    <div class="chart-legend">${legend}</div>
    <div class="chart-scroll">
      <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="日内涨跌幅折线图">
        <rect x="${x(120).toFixed(2)}" y="${pad.top}" width="${(x(135) - x(120)).toFixed(2)}" height="${innerHeight}" class="lunch-band" />
        ${grid}${axes}${lines}
      </svg>
    </div>
  </figure>`;
}

function renderGroups(groups) {
  $("#peerGroups").innerHTML = groups.map(group => {
    const strengthClass = group.strength === "强" ? "strong" : group.strength === "弱" ? "weak" : "";
    return `<article class="peer-card">
      <div class="peer-card-head">
        <div>
          <div class="holding-title"><h3>${escapeHtml(group.holding.name)}</h3><span class="code">${escapeHtml(group.holding.ts_code)}</span></div>
          <div class="cohort-note">${escapeHtml(group.benchmark)} · ${escapeHtml(group.cohort_type)} · ${escapeHtml(group.holding.shares_display)} · 成本 ${fmtNumber(group.holding.avg_cost, 3)} · 浮动 <span class="${tone(group.holding.pnl_pct)}">${fmtPct(group.holding.pnl_pct)}</span></div>
        </div>
        <div class="metric"><span class="label">强弱结论</span><span class="value"><span class="strength-badge ${strengthClass}">${escapeHtml(group.strength)}</span></span></div>
        <div class="metric"><span class="label">超额收益</span><span class="value ${tone(group.excess)}">${fmtPct(group.excess)}</span></div>
        <div class="metric"><span class="label">同行中位数</span><span class="value ${tone(group.peer_median)}">${fmtPct(group.peer_median)}</span></div>
        <div class="metric"><span class="label">组内排名</span><span class="value">${group.rank ?? "—"} / ${group.member_count || "—"}</span></div>
      </div>
      ${renderIntradayChart(group)}
      <table class="peer-table">
        <thead><tr><th>成员</th><th>价格/收盘</th><th>${state.timeframe.toUpperCase()}收益 / 隔夜</th><th>价格/均价</th><th>成交额/流通市值</th></tr></thead>
        <tbody>${memberRow(group.holding, true)}${group.peers.map(row => memberRow(row, false)).join("")}${overseasRows(group)}</tbody>
      </table>
      <div class="peer-card-foot"><span>${escapeHtml(group.matrix)}</span><span>${escapeHtml(group.cohort_status)}</span></div>
    </article>`;
  }).join("");
}

function renderUS(us) {
  $("#usDate").textContent = us.market_date
    ? `纽约市场日 ${us.market_date} · ${us.beijing_mapping}`
    : "最近完整交易日暂未取到";
  $("#usMarket").innerHTML = us.market.map(row => `<div class="tape-cell">
    <div class="tape-layer">${escapeHtml(row.layer)}</div>
    <div class="tape-name">${escapeHtml(row.name)} <span class="code">${escapeHtml(row.ticker)}</span></div>
    <div class="tape-return ${tone(row.return)}">${fmtPct(row.return)}</div>
  </div>`).join("");
  $("#usMappings").innerHTML = us.mapped_peers.length
    ? us.mapped_peers.map(group => `<div>
        <div class="mapping-head"><strong>${escapeHtml(group.holding)} 海外映射</strong><span>${escapeHtml(group.status)}</span></div>
        ${group.peers.map(row => `<div class="mapping-row"><span>${escapeHtml(row.name)} · ${escapeHtml(row.ticker)}</span><span class="mapping-role">${escapeHtml(row.role)}</span><span class="${tone(row.return)}">${fmtPct(row.return)}</span></div>`).join("")}
      </div>`).join("")
    : '<div class="empty-state" style="padding:24px">暂无已确认海外同行映射</div>';
  $("#usRule").textContent = `${us.rule} · ${us.source}`;
}

function flowValue(row) {
  if (row.value === null || row.value === undefined) return "—";
  if (row.metric === "net_amount") return `${Number(row.value).toFixed(0)} 净额原值`;
  return `${Number(row.value).toFixed(2)}%`;
}

function renderBarList(selector, rows) {
  const root = $(selector);
  if (!rows.length) {
    root.innerHTML = '<div class="empty-state">本次没有取到可用数据，未用旧值填充。</div>';
    return;
  }
  const max = Math.max(...rows.map(row => Math.abs(Number(row.value) || 0)), 1);
  root.innerHTML = rows.map(row => `<div class="bar-row">
    <span class="bar-name">${escapeHtml(row.name)}</span>
    <span class="bar-track"><span class="bar-fill" style="width:${Math.max(4, Math.abs(Number(row.value) || 0) / max * 100)}%"></span></span>
    <span class="bar-value">${flowValue(row)}</span>
  </div>`).join("");
}

function renderFlows(flow) {
  $("#flowDate").textContent = flow.trade_date
    ? `${flow.trade_date.slice(0,4)}-${flow.trade_date.slice(4,6)}-${flow.trade_date.slice(6,8)} · ${flow.source}`
    : flow.source;
  renderBarList("#industryFlow", flow.industries);
  renderBarList("#conceptFlow", flow.concepts);
}

function shanghaiDate() {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date()).map(part => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}

function journalDateLabel(date) {
  if (!date) return "选择一个日期";
  const parsed = new Date(`${date}T00:00:00+08:00`);
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "long",
    day: "numeric",
    weekday: "short",
  }).format(parsed);
}

function journalIsDirty() {
  return state.journal.date !== null && state.journal.content !== state.journal.original;
}

function setJournalStatus(message, toneName = "") {
  const status = $("#journalStatus");
  status.textContent = message;
  status.className = toneName;
}

function renderJournalList() {
  const entries = state.journal.entries;
  $("#journalCount").textContent = `${entries.length} 篇`;
  $("#journalList").innerHTML = entries.length
    ? entries.map(entry => `<button class="journal-entry${entry.date === state.journal.date ? " active" : ""}" type="button" data-journal-date="${escapeHtml(entry.date)}">
        <span class="journal-entry-date"><span>${escapeHtml(entry.date)}</span><small>${Number(entry.chars || 0).toLocaleString("zh-CN")} 字符</small></span>
        <span class="journal-entry-excerpt">${escapeHtml(entry.excerpt || "空白日记")}</span>
      </button>`).join("")
    : '<div class="journal-list-empty">日记文件夹中还没有按日期命名的 Markdown 文件。</div>';
}

function updateJournalEditor() {
  const journal = state.journal;
  $("#journalSelectedDate").textContent = journalDateLabel(journal.date);
  $("#journalFilename").textContent = journal.filename || "—";
  $("#journalStats").textContent = `${journal.content.length.toLocaleString("zh-CN")} 字符`;
  $("#journalSave").disabled = journal.loading || journal.saving || !journalIsDirty();
  $("#journalTemplate").disabled = journal.loading || journal.saving || !journal.date;
  $("#journalContent").disabled = journal.loading || journal.saving || !journal.date;
  renderJournalList();
}

function canLeaveJournal() {
  return !journalIsDirty() || window.confirm("当前日记尚未保存，确定放弃这些修改吗？");
}

async function loadJournal(date, { skipConfirm = false } = {}) {
  if (!date || (date === state.journal.date && !state.journal.loading)) return;
  if (!skipConfirm && !canLeaveJournal()) {
    $("#journalDate").value = state.journal.date || shanghaiDate();
    return;
  }
  const journal = state.journal;
  journal.loading = true;
  journal.date = date;
  journal.content = "";
  journal.original = "";
  journal.filename = `${date}.md`;
  $("#journalDate").value = date;
  $("#journalContent").value = "";
  setJournalStatus("正在读取日记…");
  updateJournalEditor();
  try {
    const response = await fetch(`/api/journal?date=${encodeURIComponent(date)}`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    journal.content = payload.content || "";
    journal.original = journal.content;
    journal.filename = payload.filename;
    journal.exists = Boolean(payload.exists);
    $("#journalContent").value = journal.content;
    setJournalStatus(payload.exists ? "已从日记文件夹载入" : "新日记 · 尚未保存", payload.exists ? "saved" : "");
  } catch (error) {
    console.error("日记读取失败", error);
    setJournalStatus(`读取失败：${error.message}`, "error");
    toast(`日记读取失败：${error.message}`);
  } finally {
    journal.loading = false;
    updateJournalEditor();
  }
}

async function loadJournalIndex() {
  try {
    const response = await fetch("/api/journals", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    state.journal.entries = payload.journals || [];
    $("#journalFolder").textContent = payload.folder || "日记/";
    renderJournalList();
    await loadJournal(payload.today || shanghaiDate(), { skipConfirm: true });
  } catch (error) {
    console.error("日记目录读取失败", error);
    $("#journalList").innerHTML = `<div class="journal-list-empty">读取失败：${escapeHtml(error.message)}</div>`;
    setJournalStatus(`连接失败：${error.message}`, "error");
  }
}

function upsertJournalEntry(payload) {
  const entry = {
    date: payload.date,
    filename: payload.filename,
    modified_at: payload.modified_at,
    chars: payload.chars,
    excerpt: payload.excerpt,
  };
  const index = state.journal.entries.findIndex(item => item.date === entry.date);
  if (index >= 0) state.journal.entries[index] = entry;
  else state.journal.entries.push(entry);
  state.journal.entries.sort((a, b) => b.date.localeCompare(a.date));
}

async function saveJournal() {
  const journal = state.journal;
  if (!journal.date || journal.loading || journal.saving || !journalIsDirty()) return;
  journal.saving = true;
  setJournalStatus("正在写入日记文件夹…");
  updateJournalEditor();
  try {
    const response = await fetch("/api/journal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ date: journal.date, content: journal.content }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    journal.original = journal.content;
    journal.filename = payload.filename;
    journal.exists = true;
    upsertJournalEntry(payload);
    setJournalStatus(`已保存 · ${new Date(payload.modified_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`, "saved");
    toast(`日记已保存到 ${payload.filename}`);
  } catch (error) {
    console.error("日记保存失败", error);
    setJournalStatus(`保存失败：${error.message}`, "error");
    toast(`日记保存失败：${error.message}`);
  } finally {
    journal.saving = false;
    updateJournalEditor();
  }
}

function insertJournalTemplate() {
  if (!state.journal.date) return;
  const existing = state.journal.content.trim();
  if (existing && !window.confirm("当前日记已有内容，是否在末尾追加复盘提纲？")) return;
  const template = `# ${state.journal.date} 交易日记\n\n## 今日事实\n- \n\n## 执行复盘\n- 做对了什么：\n- 违反了什么：\n\n## 情绪与生活\n- \n\n## 明日条件\n- 只有当……才行动：\n- 失效条件：\n`;
  state.journal.content = existing ? `${state.journal.content.trimEnd()}\n\n${template}` : template;
  $("#journalContent").value = state.journal.content;
  setJournalStatus("有未保存修改", "dirty");
  updateJournalEditor();
  $("#journalContent").focus();
}

function renderMeta(meta) {
  state.lastMeta = meta;
  const fresh = $("#freshness");
  fresh.className = `freshness ${meta.partial ? "error" : "ok"}`;
  updateFreshnessText();
  $("#truthNote").textContent = meta.truth_note;
  if (meta.errors?.length) console.warn("Dashboard data gaps:", meta.errors);
}

function render(data) {
  state.data = data;
  for (const timeframe of [...state.timeframeCache.keys()]) {
    if (timeframe !== state.timeframe) state.timeframeCache.delete(timeframe);
  }
  state.timeframeCache.set(state.timeframe, data.peer_groups);
  updateTimeframeReadyStates();
  renderMeta(data.meta);
  renderPermission(data);
  renderGroups(data.peer_groups);
  renderUS(data.us);
  renderFlows(data.money_flow);
}

function shanghaiClock() {
  const formatter = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Shanghai",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
  const parts = Object.fromEntries(formatter.formatToParts(new Date()).map(part => [part.type, part.value]));
  return {
    weekday: parts.weekday,
    minutes: Number(parts.hour) * 60 + Number(parts.minute),
  };
}

function refreshIntervalForNow() {
  const { weekday, minutes } = shanghaiClock();
  if (!["Mon", "Tue", "Wed", "Thu", "Fri"].includes(weekday)) return null;
  const live = (minutes >= 9 * 60 + 15 && minutes <= 11 * 60 + 35)
    || (minutes >= 12 * 60 + 55 && minutes <= 15 * 60 + 5);
  if (live) return LIVE_REFRESH_MS;
  if (minutes > 15 * 60 + 5 && minutes <= 16 * 60 + 30) return POST_CLOSE_REFRESH_MS;
  return null;
}

function updateFreshnessText() {
  if (!state.lastMeta) return;
  const fresh = $("#freshness");
  const time = new Date(state.lastMeta.generated_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const base = `${state.lastMeta.partial ? "部分数据可用" : "数据已更新"} · ${time}`;
  if (document.hidden) {
    fresh.querySelector("span:last-child").textContent = `${base} · 后台暂停`;
    return;
  }
  const interval = refreshIntervalForNow();
  if (!interval || !state.nextRefreshAt) {
    fresh.querySelector("span:last-child").textContent = `${base} · 非交易时段暂停`;
    return;
  }
  const seconds = Math.max(0, Math.ceil((state.nextRefreshAt - Date.now()) / 1000));
  fresh.querySelector("span:last-child").textContent = `${base} · 自动刷新 ${seconds}s`;
}

function scheduleAutoRefresh() {
  window.clearTimeout(state.refreshTimer);
  state.refreshTimer = null;
  state.nextRefreshAt = null;
  const interval = document.hidden ? null : refreshIntervalForNow();
  if (interval) {
    state.nextRefreshAt = Date.now() + interval;
    state.refreshTimer = window.setTimeout(() => loadDashboard({ background: true }), interval);
  }
  updateFreshnessText();
}

function updateTimeframeReadyStates() {
  $$('[data-timeframe]').forEach(button => {
    button.dataset.ready = state.timeframeCache.has(button.dataset.timeframe) ? "true" : "false";
  });
}

async function fetchPeerTimeframe(timeframe) {
  if (state.timeframeCache.has(timeframe)) return state.timeframeCache.get(timeframe);
  if (state.timeframeRequests.has(timeframe)) return state.timeframeRequests.get(timeframe);
  const request = fetch(`/api/peer-groups?timeframe=${encodeURIComponent(timeframe)}`, { cache: "no-store" })
    .then(async response => {
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
      state.timeframeCache.set(timeframe, payload.peer_groups);
      updateTimeframeReadyStates();
      return payload.peer_groups;
    })
    .finally(() => state.timeframeRequests.delete(timeframe));
  state.timeframeRequests.set(timeframe, request);
  return request;
}

async function prefetchTimeframes() {
  for (const timeframe of ["5d", "20d", "1d"]) {
    if (timeframe === state.timeframe || state.timeframeCache.has(timeframe)) continue;
    try {
      await fetchPeerTimeframe(timeframe);
    } catch (error) {
      console.warn(`预热${timeframe}失败`, error);
    }
  }
}

async function loadIntraday({ force = false } = {}) {
  if (state.intradayLoading) return;
  state.intradayLoading = true;
  state.intradayError = null;
  try {
    const response = await fetch(`/api/intraday${force ? "?force=1" : ""}`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    state.intraday = payload;
  } catch (error) {
    console.warn("分钟行情加载失败", error);
    state.intradayError = error.message;
  } finally {
    state.intradayLoading = false;
    const groups = state.timeframeCache.get(state.timeframe) || state.data?.peer_groups;
    if (groups) renderGroups(groups);
  }
}

async function switchTimeframe(timeframe) {
  if (timeframe === state.timeframe) return;
  state.timeframe = timeframe;
  $$('[data-timeframe]').forEach(item => item.classList.toggle("active", item.dataset.timeframe === timeframe));
  const groups = state.timeframeCache.get(timeframe);
  if (groups) {
    renderGroups(groups);
    return;
  }
  $("#peerGroups").setAttribute("aria-busy", "true");
  try {
    const loaded = await fetchPeerTimeframe(timeframe);
    if (state.timeframe === timeframe) renderGroups(loaded);
  } catch (error) {
    console.error(error);
    toast(`${timeframe.toUpperCase()}数据加载失败：${error.message}`);
  } finally {
    $("#peerGroups").removeAttribute("aria-busy");
  }
}

async function loadDashboard({ showToast = false, force = false, background = false } = {}) {
  if (state.loading) return;
  state.loading = true;
  const button = $("#refreshButton");
  if (!background) {
    button.disabled = true;
    button.textContent = "更新中…";
  }
  try {
    const response = await fetch(`/api/dashboard?timeframe=${encodeURIComponent(state.timeframe)}${force ? "&force=1" : ""}`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    render(payload);
    void loadIntraday({ force });
    void prefetchTimeframes();
    if (showToast) toast(payload.meta.partial ? "已刷新，部分数据存在缺口" : "数据已刷新");
  } catch (error) {
    console.error(error);
    const fresh = $("#freshness");
    fresh.className = "freshness error";
    fresh.querySelector("span:last-child").textContent = "连接失败";
    toast(`数据连接失败：${error.message}`);
  } finally {
    state.loading = false;
    if (!background) {
      button.disabled = false;
      button.textContent = "刷新数据";
    }
    scheduleAutoRefresh();
  }
}

$("#refreshButton").addEventListener("click", () => loadDashboard({ showToast: true, force: true }));
$$('[data-timeframe]').forEach(button => button.addEventListener("click", () => switchTimeframe(button.dataset.timeframe)));
$("#journalToday").addEventListener("click", () => loadJournal(shanghaiDate()));
$("#journalDate").addEventListener("change", event => loadJournal(event.target.value));
$("#journalList").addEventListener("click", event => {
  const entry = event.target.closest("[data-journal-date]");
  if (entry) void loadJournal(entry.dataset.journalDate);
});
$("#journalContent").addEventListener("input", event => {
  state.journal.content = event.target.value;
  setJournalStatus(journalIsDirty() ? "有未保存修改" : "内容未变化", journalIsDirty() ? "dirty" : "");
  updateJournalEditor();
});
$("#journalSave").addEventListener("click", saveJournal);
$("#journalTemplate").addEventListener("click", insertJournalTemplate);
document.addEventListener("keydown", event => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s" && document.activeElement === $("#journalContent")) {
    event.preventDefault();
    void saveJournal();
  }
});
window.addEventListener("beforeunload", event => {
  if (!journalIsDirty()) return;
  event.preventDefault();
  event.returnValue = "";
});

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    scheduleAutoRefresh();
  } else if (state.data) {
    void loadDashboard({ background: true });
  }
});

const sections = $$('main section[id]');
const navLinks = $$('.nav-link');
let navFrame = null;
function updateActiveNavigation() {
  navFrame = null;
  const marker = window.innerHeight * .34;
  const active = sections.find(section => {
    const rect = section.getBoundingClientRect();
    return rect.top <= marker && rect.bottom > marker;
  }) || sections.reduce((nearest, section) => {
    const distance = Math.abs(section.getBoundingClientRect().top - marker);
    return !nearest || distance < nearest.distance ? { section, distance } : nearest;
  }, null)?.section;
  if (!active) return;
  navLinks.forEach(link => link.classList.toggle("active", link.getAttribute("href") === `#${active.id}`));
}
window.addEventListener("scroll", () => {
  if (!navFrame) navFrame = window.requestAnimationFrame(updateActiveNavigation);
}, { passive: true });
window.addEventListener("hashchange", updateActiveNavigation);

state.countdownTimer = window.setInterval(updateFreshnessText, 1000);
updateTimeframeReadyStates();
void loadJournalIndex();
loadDashboard();
updateActiveNavigation();

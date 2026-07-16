const state = { timeframe: "1d", loading: false, data: null };

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
  $("#holdingTruth").innerHTML = `
    <div>${escapeHtml(data.holdings_status.confirmed)}</div>
    <div class="danger">${escapeHtml(data.holdings_status.unresolved)}</div>
    <div>组合盈亏：未启用，直到仓位核对完成</div>`;
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

function renderGroups(groups) {
  $("#peerGroups").innerHTML = groups.map(group => {
    const strengthClass = group.strength === "强" ? "strong" : group.strength === "弱" ? "weak" : "";
    return `<article class="peer-card">
      <div class="peer-card-head">
        <div>
          <div class="holding-title"><h3>${escapeHtml(group.holding.name)}</h3><span class="code">${escapeHtml(group.holding.ts_code)}</span></div>
          <div class="cohort-note">${escapeHtml(group.benchmark)} · ${escapeHtml(group.cohort_type)} · ${escapeHtml(group.holding.shares_display)}</div>
        </div>
        <div class="metric"><span class="label">强弱结论</span><span class="value"><span class="strength-badge ${strengthClass}">${escapeHtml(group.strength)}</span></span></div>
        <div class="metric"><span class="label">超额收益</span><span class="value ${tone(group.excess)}">${fmtPct(group.excess)}</span></div>
        <div class="metric"><span class="label">同行中位数</span><span class="value ${tone(group.peer_median)}">${fmtPct(group.peer_median)}</span></div>
        <div class="metric"><span class="label">组内排名</span><span class="value">${group.rank ?? "—"} / ${group.member_count || "—"}</span></div>
      </div>
      <table class="peer-table">
        <thead><tr><th>成员</th><th>现价</th><th>${state.timeframe.toUpperCase()}收益</th><th>价格/均价</th><th>成交额/流通市值</th></tr></thead>
        <tbody>${memberRow(group.holding, true)}${group.peers.map(row => memberRow(row, false)).join("")}</tbody>
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
  if (row.metric === "net_amount") return `${Number(row.value).toFixed(0)} 万元`;
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

function renderRecovery(recovery) {
  const saved = JSON.parse(localStorage.getItem("trading-recovery-checks") || "{}");
  $("#recoveryRule").textContent = recovery.rule;
  $("#recoveryChecklist").innerHTML = recovery.checklist.map((item, index) => `<label class="check-item">
    <input type="checkbox" data-check="${index}" ${saved[index] ? "checked" : ""}>
    <span>${escapeHtml(item)}</span>
  </label>`).join("");
  const update = () => {
    const values = {};
    $$('[data-check]').forEach(input => { values[input.dataset.check] = input.checked; });
    localStorage.setItem("trading-recovery-checks", JSON.stringify(values));
    $("#checkProgress").textContent = `${Object.values(values).filter(Boolean).length} / ${recovery.checklist.length}`;
  };
  $$('[data-check]').forEach(input => input.addEventListener("change", update));
  update();
}

function renderMeta(meta) {
  const fresh = $("#freshness");
  fresh.className = `freshness ${meta.partial ? "error" : "ok"}`;
  const time = new Date(meta.generated_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  fresh.querySelector("span:last-child").textContent = `${meta.partial ? "部分数据可用" : "数据已更新"} · ${time}`;
  $("#truthNote").textContent = meta.truth_note;
  if (meta.errors?.length) console.warn("Dashboard data gaps:", meta.errors);
}

function render(data) {
  state.data = data;
  renderMeta(data.meta);
  renderPermission(data);
  renderGroups(data.peer_groups);
  renderUS(data.us);
  renderFlows(data.money_flow);
  renderRecovery(data.recovery);
}

async function loadDashboard(showToast = false) {
  if (state.loading) return;
  state.loading = true;
  const button = $("#refreshButton");
  button.disabled = true;
  button.textContent = "更新中…";
  try {
    const response = await fetch(`/api/dashboard?timeframe=${encodeURIComponent(state.timeframe)}`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    render(payload);
    if (showToast) toast(payload.meta.partial ? "已刷新，部分数据存在缺口" : "数据已刷新");
  } catch (error) {
    console.error(error);
    const fresh = $("#freshness");
    fresh.className = "freshness error";
    fresh.querySelector("span:last-child").textContent = "连接失败";
    toast(`数据连接失败：${error.message}`);
  } finally {
    state.loading = false;
    button.disabled = false;
    button.textContent = "刷新数据";
  }
}

$("#refreshButton").addEventListener("click", () => loadDashboard(true));
$$('[data-timeframe]').forEach(button => button.addEventListener("click", () => {
  if (button.dataset.timeframe === state.timeframe) return;
  state.timeframe = button.dataset.timeframe;
  $$('[data-timeframe]').forEach(item => item.classList.toggle("active", item === button));
  loadDashboard(false);
}));

const sections = $$('main section[id]');
const navLinks = $$('.nav-link');
const observer = new IntersectionObserver(entries => {
  const visible = entries.filter(entry => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
  if (!visible) return;
  navLinks.forEach(link => link.classList.toggle("active", link.getAttribute("href") === `#${visible.target.id}`));
}, { rootMargin: "-20% 0px -65% 0px", threshold: [0, .2, .6] });
sections.forEach(section => observer.observe(section));

loadDashboard(false);

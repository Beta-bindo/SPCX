#!/usr/bin/env python3
"""Patch admin HTML templates to unified left-sidebar layout (same as roles.html)."""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
TEMPLATES = ROOT / "app" / "templates"

LOGIN_CARD = """    <div id="loginCard" class="card login-card">
      <h1 class="login-heading">交易助手 · 运营后台</h1>
      <p class="sub">使用后台账号登录（默认用户名 <code>admin</code>）</p>
      <div class="row">
        <input id="adminUsername" type="text" placeholder="用户名" value="admin" style="min-width:140px" autocomplete="username" />
        <input id="adminPassword" type="password" placeholder="密码" style="min-width:220px" autocomplete="current-password" />
        <button id="loginBtn" type="button">登录</button>
      </div>
      <p id="loginErr" class="err hidden"></p>
    </div>"""

SIDEBAR = """      <aside id="adminSidebar" class="admin-sidebar">
        <div class="sidebar-head">
          <span class="sidebar-brand">运营后台</span>
          <button type="button" id="sidebarToggle" class="sidebar-toggle secondary" title="收起侧栏" aria-label="收起侧栏">◀</button>
        </div>
        <nav id="adminNavLinks" class="sidebar-nav" aria-label="后台导航"></nav>
        <div class="sidebar-foot">
          <div id="adminUserLabel" class="sidebar-user"></div>
          <div class="sidebar-actions">
            <button type="button" class="secondary sidebar-foot-btn" title="修改密码" onclick="openChangePasswordDialog(localStorage.getItem('ta_admin_token')||'')"><span class="sidebar-icon">🔑</span><span class="sidebar-label">改密</span></button>
            <button type="button" class="secondary sidebar-foot-btn" title="退出登录" onclick="logout()"><span class="sidebar-icon">⏻</span><span class="sidebar-label">退出</span></button>
          </div>
        </div>
      </aside>"""

BOOT_TAIL = """
wireAdminLoginButton(async (token) => {
  adminToken = token;
  renderAdminNav(ACTIVE_MODULE);
  await bootPanel();
});

initAdminPage(ACTIVE_MODULE, async (token) => {
  adminToken = token;
  await bootPanel();
});"""


def shell(title: str, heading: str, subtitle: str, active_module: str, main_body: str, script_body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="color-scheme" content="light dark" />
  <title>{title}</title>
  <link rel="stylesheet" href="/static/admin_common.css" />
</head>
<body>
  <div class="wrap">
{LOGIN_CARD}

    <div id="panel" class="hidden admin-shell">
{SIDEBAR}
      <main class="admin-main">
        <header class="admin-page-head">
          <h1>{heading}</h1>
          <p class="sub">{subtitle}</p>
        </header>

{main_body}
      </main>
    </div>
  </div>

<script src="/static/admin_common.js?v=12"></script>
<script>
const ACTIVE_MODULE = '{active_module}';
{script_body}
{BOOT_TAIL}
</script>
</body>
</html>
"""


ADMIN_MAIN = """      <div class="card stats" id="stats"></div>

      <div class="card filter-bar">
        <label>筛选
          <select id="statusFilter" onchange="loadDevices(1)">
            <option value="" selected>全部</option>
            <option value="pending">待审核</option>
            <option value="approved">已通过</option>
            <option value="rejected">已拒绝</option>
            <option value="disabled">已停用</option>
          </select>
        </label>
        <label>搜索
          <input id="searchInput" type="search" placeholder="昵称 / 手机 / 机器码 / 账号" style="min-width:240px" />
        </label>
        <label class="modal-check" style="margin:0">
          <input id="expiringFilter" type="checkbox" onchange="loadDevices(1)" /> 仅看 7 天内到期
        </label>
        <div class="filter-actions">
          <button class="secondary" onclick="loadDevices(1)">查询</button>
          <button class="secondary" onclick="resetFilters()">重置</button>
        </div>
      </div>

      <div class="card">
        <table>
          <thead>
            <tr>
              <th>昵称</th><th>联系方式</th><th>BA账号</th><th>EX账号</th><th>在线</th><th>授权到期</th><th>机器码</th><th>状态</th><th>最后在线</th><th>操作</th>
            </tr>
          </thead>
          <tbody id="deviceRows"></tbody>
        </table>
        <div class="pager">
          <button class="secondary" id="prevBtn" onclick="loadDevices(devicePage - 1)">上一页</button>
          <span id="pageInfo" class="sub"></span>
          <button class="secondary" id="nextBtn" onclick="loadDevices(devicePage + 1)">下一页</button>
          <label class="pager-size">每页
            <select id="pageSize" onchange="loadDevices(1)">
              <option value="10">10</option>
              <option value="20" selected>20</option>
              <option value="50">50</option>
            </select>
          </label>
        </div>
      </div>"""

ADMIN_SCRIPT = """let adminToken = '';
let devicePage = 1;

function logout() { logoutAdmin(); }

function resetFilters() {
  document.getElementById('statusFilter').value = '';
  document.getElementById('searchInput').value = '';
  document.getElementById('expiringFilter').checked = false;
  document.getElementById('pageSize').value = '20';
  loadDevices(1);
}

async function refreshAll() {
  await loadStats();
  await loadDevices(1);
}

async function loadStats() {
  const res = await fetch('/api/v1/admin/stats', { headers: authHeaders(adminToken) });
  if (res.status === 401) return logout();
  const data = await res.json();
  const by = data.devices_by_status || {};
  document.getElementById('stats').innerHTML = `
    <div class="stat"><span class="sub">待审核</span><b>${by.pending||0}</b></div>
    <div class="stat"><span class="sub">已通过</span><b>${by.approved||0}</b></div>
    <div class="stat"><span class="sub">在线</span><b>${data.online||0}</b></div>
    <div class="stat"><span class="sub">7天内到期</span><b class="${(data.expiring_soon||0)>0?'warn':''}">${data.expiring_soon||0}</b></div>
    <div class="stat"><span class="sub">已到期</span><b class="${(data.expired||0)>0?'err':''}">${data.expired||0}</b></div>
    <div class="stat"><span class="sub">已拒绝</span><b>${by.rejected||0}</b></div>
    <div class="stat"><span class="sub">已停用</span><b>${by.disabled||0}</b></div>
    <div class="stat"><span class="sub">交易笔数</span><b>${data.trade_count||0}</b></div>
    <div class="stat"><span class="sub">累计净利</span><b>$${data.total_net_pnl||0}</b></div>
  `;
}

function onlineLabel(device) {
  return onlineDot(device);
}

function expiresLabel(device) {
  if (!device.expires_at) return '永久';
  const text = fmtBeijing(device.expires_at);
  if (device.expired) return `<span class="err">${esc(text)} 已到期</span>`;
  return esc(text);
}

async function updateDevice(deviceId, payload) {
  const res = await fetch(deviceApiPath(deviceId, 'update'), {
    method: 'POST',
    headers: authHeaders(adminToken),
    body: JSON.stringify(payload),
  });
  if (res.status === 401) return logout();
  if (!res.ok) {
    alert(await readError(res) || '更新失败');
    return null;
  }
  const data = await res.json();
  return data.device || null;
}

async function editDisplayName(deviceId, current) {
  const name = await openDisplayNameDialog({
    title: '修改昵称',
    currentValue: current || '',
  });
  if (name === null) return;
  const updated = await updateDevice(deviceId, { display_name: name });
  if (updated) await loadDevices(devicePage);
}

async function editExpiresAt(deviceId, current) {
  const value = await openExpiresAtDialog({
    title: '设置授权到期时间',
    currentValue: current || '',
    defaultPermanent: !current,
  });
  if (value === null) return;
  const updated = await updateDevice(deviceId, { expires_at: value });
  if (updated) await loadDevices(devicePage);
}

async function setAccountStatus(deviceId, platform, action) {
  const res = await fetch(accountApiPath(deviceId, platform, action), {
    method: 'POST',
    headers: authHeaders(adminToken),
  });
  if (res.status === 401) return logout();
  if (!res.ok) {
    alert(await readError(res) || '操作失败');
    return;
  }
  await loadDevices(devicePage);
}

function accountCell(account, status, platform) {
  const acct = account || '-';
  const st = status || 'pending';
  const enableAct = st === 'enabled' ? 'disable' : 'enable';
  const enableLabel = st === 'enabled' ? '停用' : '启用';
  const platformLabel = platform === 'ba' ? 'BA' : 'EX';
  return `
    <div class="cell-title"><code>${esc(acct)}</code></div>
    <div><span class="badge ${accountStatusClass(st)}">${accountStatusLabel(st)}</span></div>
    <div class="cell-actions">
      <button class="btn-inline secondary" type="button" data-acct-act="${platform}:${enableAct}">${platformLabel}${enableLabel}</button>
    </div>`;
}

async function loadDevices(page) {
  const status = document.getElementById('statusFilter').value;
  const pageSize = document.getElementById('pageSize').value;
  const q = document.getElementById('searchInput').value.trim();
  const expiring = document.getElementById('expiringFilter').checked;
  devicePage = Math.max(1, page);
  let url = `/api/v1/admin/devices?page=${devicePage}&page_size=${pageSize}`;
  if (status) url += `&status=${encodeURIComponent(status)}`;
  if (q) url += `&q=${encodeURIComponent(q)}`;
  if (expiring) url += `&expiring=7`;
  const res = await fetch(url, { headers: authHeaders(adminToken) });
  if (res.status === 401) return logout();
  const data = await res.json();
  devicePage = data.page || devicePage;
  const tbody = document.getElementById('deviceRows');
  tbody.innerHTML = '';
  for (const d of data.devices || []) {
    const tr = document.createElement('tr');
    const id = d.device_id;
    tr.innerHTML = `
      <td>
        <div class="cell-title">${esc(d.display_name)}</div>
        <div class="sub cell-note">${esc(d.note || '无备注')}</div>
        <div class="cell-actions">
          <button class="btn-inline secondary" type="button" data-action="rename">改昵称</button>
        </div>
      </td>
      <td>${esc(d.contact||'-')}</td>
      <td>${accountCell(d.ba_account, d.ba_account_status, 'ba')}</td>
      <td>${accountCell(d.mt5_account, d.ex_account_status, 'ex')}</td>
      <td>${onlineLabel(d)}</td>
      <td>
        <div class="cell-title">${expiresLabel(d)}</div>
        <div class="cell-actions">
          <button class="btn-inline secondary" type="button" data-action="expires">设到期</button>
        </div>
      </td>
      <td><code>${esc(d.device_id.slice(0,12))}…</code></td>
      <td>
        <div><span class="badge ${d.status}">${statusLabel(d.status)}</span></div>
        ${d.status === 'approved' ? `<div class="sub" style="margin-top:4px">自动下单：<span class="badge ${d.auto_trade_enabled ? 'approved' : 'disabled'}">${d.auto_trade_enabled ? '已开通' : '未开通'}</span></div>` : ''}
      </td>
      <td>${esc(fmtBeijing(d.last_seen_at))}</td>
      <td>${actionButtons(d)}</td>`;
    tr.querySelector('[data-action="rename"]')?.addEventListener('click', () => {
      editDisplayName(id, d.display_name || '');
    });
    tr.querySelector('[data-action="expires"]')?.addEventListener('click', () => {
      editExpiresAt(id, d.expires_at || '');
    });
    tr.querySelectorAll('[data-act]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const a = btn.getAttribute('data-act');
        if (a === 'reject') reject(id);
        else if (a === 'delete') removeDevice(id);
        else if (a === 'auto-on') setAutoTrade(id, true);
        else if (a === 'auto-off') setAutoTrade(id, false);
        else act(id, a);
      });
    });
    tr.querySelectorAll('[data-acct-act]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const raw = btn.getAttribute('data-acct-act') || '';
        const [platform, action] = raw.split(':');
        if (platform && action) setAccountStatus(id, platform, action);
      });
    });
    tbody.appendChild(tr);
  }
  const pages = data.pages || 1;
  document.getElementById('pageInfo').textContent = `第 ${devicePage} / ${pages} 页 · 共 ${data.total||0} 条`;
  document.getElementById('prevBtn').disabled = devicePage <= 1;
  document.getElementById('nextBtn').disabled = devicePage >= pages;
}

function actionButtons(d) {
  const btns = [];
  if (d.status === 'pending') {
    btns.push('<button class="ok" data-act="approve">通过</button>');
    btns.push('<button class="danger" data-act="reject">拒绝</button>');
  }
  if (d.status === 'approved') {
    if (d.auto_trade_enabled) {
      btns.push('<button class="secondary" data-act="auto-off">关闭自动下单</button>');
    } else {
      btns.push('<button class="ok" data-act="auto-on">开通自动下单</button>');
    }
    btns.push('<button class="danger" data-act="disable">停用</button>');
  }
  if (d.status === 'disabled' || d.status === 'rejected') {
    btns.push('<button class="ok" data-act="approve">重新通过</button>');
  }
  btns.push('<button class="danger" data-act="delete">删除</button>');
  return btns.join(' ');
}

async function act(deviceId, action) {
  let body = {};
  if (action === 'approve') {
    const expiresAt = await openExpiresAtDialog({
      title: '通过授权 · 设置到期时间',
      defaultPermanent: true,
    });
    if (expiresAt === null) return;
    body.expires_at = expiresAt;
  }
  const res = await fetch(deviceApiPath(deviceId, action), {
    method: 'POST', headers: authHeaders(adminToken), body: JSON.stringify(body)
  });
  if (!res.ok) { alert('操作失败'); return; }
  await refreshAll();
}

async function setAutoTrade(deviceId, enabled) {
  const action = enabled ? 'enable' : 'disable';
  const res = await fetch(autoTradeApiPath(deviceId, action), {
    method: 'POST', headers: authHeaders(adminToken)
  });
  if (res.status === 401) return logout();
  if (!res.ok) { alert(await readError(res) || '操作失败'); return; }
  await loadDevices(devicePage);
}

async function reject(deviceId) {
  const reason = prompt('拒绝原因（可选）') || '管理员拒绝';
  const res = await fetch(deviceApiPath(deviceId, 'reject'), {
    method: 'POST', headers: authHeaders(adminToken), body: JSON.stringify({ reason })
  });
  if (!res.ok) { alert('操作失败'); return; }
  await refreshAll();
}

async function removeDevice(deviceId) {
  if (!confirm('确定删除该设备？其上报的交易记录也会一并删除。')) return;
  const res = await fetch(deviceApiPath(deviceId), {
    method: 'DELETE', headers: authHeaders(adminToken)
  });
  if (!res.ok) { alert('删除失败'); return; }
  await refreshAll();
}

document.getElementById('searchInput').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') loadDevices(1);
});

async function bootPanel() {
  await refreshAll();
}"""

DASHBOARD_MAIN = """      <div class="card stats" id="stats"></div>

      <div class="card">
        <div class="filter-bar" style="margin-bottom:12px">
          <label>区间
            <select id="daysSel" onchange="loadDashboard()">
              <option value="7">近 7 天</option>
              <option value="30" selected>近 30 天</option>
              <option value="90">近 90 天</option>
            </select>
          </label>
          <div class="filter-actions">
            <button class="secondary" onclick="loadDashboard()">刷新</button>
          </div>
        </div>
        <h2 style="font-size:1rem;margin:4px 0">每日净利（柱）与成交笔数（线）</h2>
        <div class="chart-legend">
          <span><i style="background:#22c55e"></i>盈利日净利</span>
          <span><i style="background:#ef4444"></i>亏损日净利</span>
          <span><i style="background:#3b82f6"></i>成交笔数</span>
        </div>
        <div class="chart-wrap"><div id="chart"></div></div>
      </div>

      <div class="card">
        <h2 style="font-size:1rem;margin:4px 0">净利 Top 10 用户</h2>
        <table>
          <thead><tr><th>用户</th><th>成交笔数</th><th>累计净利</th></tr></thead>
          <tbody id="topRows"></tbody>
        </table>
      </div>"""

DASHBOARD_SCRIPT = """let adminToken = '';

function logout() { logoutAdmin(); }

async function refreshAll() {
  await loadStats();
  await loadDashboard();
}

async function loadStats() {
  const res = await fetch('/api/v1/admin/stats', { headers: authHeaders(adminToken) });
  if (res.status === 401) return logout();
  const data = await res.json();
  const by = data.devices_by_status || {};
  document.getElementById('stats').innerHTML = `
    <div class="stat"><span class="sub">已通过</span><b>${by.approved||0}</b></div>
    <div class="stat"><span class="sub">在线</span><b>${data.online||0}</b></div>
    <div class="stat"><span class="sub">待审核</span><b>${by.pending||0}</b></div>
    <div class="stat"><span class="sub">7天内到期</span><b class="${(data.expiring_soon||0)>0?'warn':''}">${data.expiring_soon||0}</b></div>
    <div class="stat"><span class="sub">已到期</span><b class="${(data.expired||0)>0?'err':''}">${data.expired||0}</b></div>
    <div class="stat"><span class="sub">交易笔数</span><b>${data.trade_count||0}</b></div>
    <div class="stat"><span class="sub">累计净利</span><b>$${data.total_net_pnl||0}</b></div>
  `;
}

async function loadDashboard() {
  const days = document.getElementById('daysSel').value;
  const res = await fetch(`/api/v1/admin/dashboard?days=${days}`, { headers: authHeaders(adminToken) });
  if (res.status === 401) return logout();
  const data = await res.json();
  renderChart(data.daily || []);
  renderTop(data.top_devices || []);
}

function renderChart(daily) {
  const W = Math.max(640, daily.length * 22);
  const H = 260, padL = 48, padR = 16, padT = 16, padB = 36;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const nets = daily.map(d => d.net_pnl);
  const counts = daily.map(d => d.count);
  const maxNet = Math.max(1, ...nets.map(Math.abs));
  const maxCnt = Math.max(1, ...counts);
  const n = daily.length || 1;
  const bw = plotW / n;
  const zeroY = padT + plotH * (maxNet / (2 * maxNet));

  let bars = '', line = '', dots = '', labels = '';
  daily.forEach((d, i) => {
    const x = padL + i * bw;
    const h = (Math.abs(d.net_pnl) / (2 * maxNet)) * plotH;
    const y = d.net_pnl >= 0 ? zeroY - h : zeroY;
    bars += `<rect class="${d.net_pnl>=0?'bar-pos':'bar-neg'}" x="${(x+bw*0.15).toFixed(1)}" y="${y.toFixed(1)}" width="${(bw*0.7).toFixed(1)}" height="${Math.max(0.5,h).toFixed(1)}"><title>${d.day}\\n净利 $${d.net_pnl}\\n${d.count} 笔</title></rect>`;
    const cx = x + bw / 2;
    const cy = padT + plotH - (d.count / maxCnt) * plotH;
    line += `${i === 0 ? 'M' : 'L'}${cx.toFixed(1)},${cy.toFixed(1)} `;
    dots += `<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="2" fill="#3b82f6"><title>${d.day}: ${d.count} 笔</title></circle>`;
    if (n <= 31 || i % Math.ceil(n / 15) === 0) {
      labels += `<text class="lbl" x="${cx.toFixed(1)}" y="${(H-8).toFixed(1)}" text-anchor="middle">${d.day.slice(5)}</text>`;
    }
  });
  const svg = `
    <svg class="chart-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMinYMid meet">
      <line class="axis" x1="${padL}" y1="${zeroY.toFixed(1)}" x2="${W-padR}" y2="${zeroY.toFixed(1)}" />
      ${bars}
      <path class="line" d="${line.trim()}" />
      ${dots}
      ${labels}
      <text class="lbl" x="4" y="${(padT+8)}">+$${maxNet.toFixed(0)}</text>
      <text class="lbl" x="4" y="${(padT+plotH).toFixed(1)}">-$${maxNet.toFixed(0)}</text>
    </svg>`;
  document.getElementById('chart').innerHTML = svg;
}

function renderTop(rows) {
  const tbody = document.getElementById('topRows');
  tbody.innerHTML = '';
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="3" class="sub">暂无成交数据</td></tr>';
    return;
  }
  for (const r of rows) {
    const tr = document.createElement('tr');
    const cls = r.net_pnl >= 0 ? '' : 'err';
    tr.innerHTML = `<td>${esc(r.display_name || '-')}</td><td>${r.count}</td><td class="${cls}">$${r.net_pnl}</td>`;
    tbody.appendChild(tr);
  }
}

async function bootPanel() {
  await refreshAll();
}"""

TRADES_MAIN = """      <div class="card filter-bar">
        <label>用户
          <select id="deviceFilter" onchange="loadTrades(1)">
            <option value="">全部用户</option>
          </select>
        </label>
        <label>品种
          <select id="presetFilter" onchange="loadTrades(1)">
            <option value="">全部品种</option>
            <option value="xau">黄金 XAU</option>
            <option value="xag">白银 XAG</option>
          </select>
        </label>
        <label>模式
          <select id="modeFilter" onchange="loadTrades(1)">
            <option value="">全部模式</option>
            <option value="contraction">收缩</option>
            <option value="expansion">扩张</option>
          </select>
        </label>
        <label class="date-field">开始日期
          <input id="dateFrom" class="dt-input date-input" type="date" />
        </label>
        <label class="date-field">结束日期
          <input id="dateTo" class="dt-input date-input" type="date" />
        </label>
        <label>盈亏
          <select id="pnlFilter" onchange="loadTrades(1)">
            <option value="">全部</option>
            <option value="profit">盈利</option>
            <option value="loss">亏损</option>
            <option value="flat">持平</option>
          </select>
        </label>
        <div class="filter-actions">
          <button class="secondary" onclick="loadTrades(tradePage)">刷新</button>
          <button class="secondary" onclick="resetFilters()">重置</button>
          <button onclick="exportTrades()">导出 CSV</button>
        </div>
      </div>

      <div class="card">
        <div id="tradeSummary" class="summary-grid"></div>
        <table>
          <thead>
            <tr>
              <th>用户</th><th>机器码</th><th>类型</th><th>品种</th><th>模式</th><th>时间</th>
              <th>点差</th><th>BA价</th><th>Ex价</th><th>BA量</th><th>Ex量</th><th>方向</th>
              <th>BA盈亏</th><th>Ex盈亏</th><th>BA手续费</th><th>Ex手续费</th><th>BA资金费</th><th>BA返佣</th><th>净利</th><th>上报时间</th>
            </tr>
          </thead>
          <tbody id="tradeRows"></tbody>
        </table>
        <div class="pager">
          <button class="secondary" id="prevBtn" onclick="loadTrades(tradePage - 1)">上一页</button>
          <span id="pageInfo" class="sub"></span>
          <button class="secondary" id="nextBtn" onclick="loadTrades(tradePage + 1)">下一页</button>
          <label class="pager-size">每页
            <select id="pageSize" onchange="loadTrades(1)">
              <option value="20">20</option>
              <option value="50" selected>50</option>
              <option value="100">100</option>
            </select>
          </label>
        </div>
      </div>"""

TRADES_SCRIPT = """let adminToken = '';
let tradePage = 1;

function logout() { logoutAdmin(); }

function initFilters() {
  try {
    initTradeDateFilters(() => loadTrades(1));
  } catch (err) {
    console.error(err);
    alert('筛选控件初始化失败，请刷新页面重试');
  }
}

function resetFilters() {
  document.getElementById('deviceFilter').value = '';
  document.getElementById('presetFilter').value = '';
  document.getElementById('modeFilter').value = '';
  document.getElementById('pnlFilter').value = '';
  document.getElementById('pageSize').value = '50';
  resetTradeDateDefaults();
  loadTrades(1);
}

async function loadDeviceOptions() {
  const res = await fetch('/api/v1/admin/devices?page=1&page_size=500', { headers: authHeaders(adminToken) });
  if (res.status === 401) return logout();
  if (!res.ok) { alert(await readError(res) || '加载用户失败'); return; }
  const data = await res.json();
  const sel = document.getElementById('deviceFilter');
  const current = sel.value;
  sel.innerHTML = '<option value="">全部用户</option>';
  for (const d of data.devices || []) {
    const opt = document.createElement('option');
    opt.value = d.device_id;
    opt.textContent = `${d.display_name || d.device_id.slice(0, 8)} (${statusLabel(d.status)})`;
    sel.appendChild(opt);
  }
  sel.value = current;
}

async function loadTrades(page) {
  const pageSize = document.getElementById('pageSize').value;
  tradePage = Math.max(1, page);
  let url = `/api/v1/admin/trades?page=${tradePage}&page_size=${pageSize}&${buildTradeQuery()}`;
  const res = await fetch(url, { headers: authHeaders(adminToken) });
  if (res.status === 401) return logout();
  if (!res.ok) { alert(await readError(res) || '加载交易明细失败'); return; }
  const data = await res.json();
  tradePage = data.page || tradePage;
  const summary = data.summary || {};
  const net = Number(summary.net_pnl || 0);
  document.getElementById('tradeSummary').innerHTML = `
    <div class="summary-item"><span class="sub">筛选笔数</span><b>${summary.count || data.total || 0}</b></div>
    <div class="summary-item"><span class="sub">BA 盈亏合计</span><b class="${Number(summary.ba_pnl||0)>=0?'pos':'neg'}">${fmtMoney(summary.ba_pnl)}</b></div>
    <div class="summary-item"><span class="sub">EX 盈亏合计</span><b class="${Number(summary.mt5_pnl||0)>=0?'pos':'neg'}">${fmtMoney(summary.mt5_pnl)}</b></div>
    <div class="summary-item"><span class="sub">BA 手续费合计</span><b>${fmtMoney(summary.ba_fee)}</b></div>
    <div class="summary-item"><span class="sub">EX 手续费合计</span><b>${fmtMoney(summary.mt5_fee)}</b></div>
    <div class="summary-item"><span class="sub">BA 资金费合计</span><b>${fmtMoney(summary.ba_funding_fee)}</b></div>
    <div class="summary-item"><span class="sub">BA 返佣合计</span><b>${fmtMoney(summary.ba_rebate)}</b></div>
    <div class="summary-item"><span class="sub">净利合计</span><b class="${net>=0?'pos':'neg'}">${fmtMoney(summary.net_pnl)}</b></div>
  `;
  const tbody = document.getElementById('tradeRows');
  tbody.innerHTML = '';
  const rows = data.trades || [];
  if (!rows.length) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td colspan="20" class="sub" style="text-align:center;padding:24px">暂无数据。请点「重置」扩大日期范围，或确认用户筛选为「全部用户」。</td>`;
    tbody.appendChild(tr);
  }
  for (const t of rows) {
    const rowNet = t.net_pnl || 0;
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${esc(t.display_name||'-')}</td>
      <td><code>${esc((t.device_id||'').slice(0,12))}…</code></td>
      <td>${esc(actionLabel(t.action))}</td>
      <td>${esc(presetLabel(t.preset_id))}</td>
      <td>${esc(modeLabel(t.mode))}</td>
      <td>${esc(fmtBeijing(t.settled_at))}</td>
      <td>${fmtSpread(t.spread)}</td>
      <td>${fmtPrice(t.ba_price)}</td>
      <td>${fmtPrice(t.ex_price)}</td>
      <td>${fmtQty(t.ba_quantity)}</td>
      <td>${fmtQty(t.mt5_quantity)}</td>
      <td>${esc(t.direction || '-')}</td>
      <td>${fmtTradePnl(t.action, t.ba_pnl)}</td>
      <td>${fmtTradePnl(t.action, t.mt5_pnl)}</td>
      <td>${fmtMoney(t.ba_fee)}</td>
      <td>${fmtMoney(t.mt5_fee)}</td>
      <td>${fmtFundingFee(t.action, t.ba_funding_fee)}</td>
      <td>${fmtFundingFee(t.action, t.ba_rebate)}</td>
      <td class="${rowNet>=0?'pos':'neg'}">${fmtTradePnl(t.action, rowNet)}</td>
      <td>${esc(fmtBeijing(t.uploaded_at))}</td>`;
    tbody.appendChild(tr);
  }
  const pages = data.pages || 1;
  document.getElementById('pageInfo').textContent = `第 ${tradePage} / ${pages} 页 · 共 ${data.total||0} 条`;
  document.getElementById('prevBtn').disabled = tradePage <= 1;
  document.getElementById('nextBtn').disabled = tradePage >= pages;
}

function buildTradeQuery() {
  const params = new URLSearchParams();
  const deviceId = document.getElementById('deviceFilter').value;
  const presetId = document.getElementById('presetFilter').value;
  const mode = document.getElementById('modeFilter').value;
  const pnl = document.getElementById('pnlFilter').value;
  const dateFrom = getTradeDateFilterValue('dateFrom', 'start');
  const dateTo = getTradeDateFilterValue('dateTo', 'end');
  if (deviceId) params.set('device_id', deviceId);
  if (presetId) params.set('preset_id', presetId);
  if (mode) params.set('mode', mode);
  if (dateFrom) params.set('date_from', dateFrom);
  if (dateTo) params.set('date_to', dateTo);
  if (pnl) params.set('pnl', pnl);
  return params.toString();
}

function exportTrades() {
  if (!adminToken) return;
  const url = `/api/v1/admin/trades/export?${buildTradeQuery()}`;
  fetch(url, { headers: authHeaders(adminToken) })
    .then(async res => {
      if (res.status === 401) return logout();
      if (!res.ok) throw new Error(await readError(res) || '导出失败');
      return res.blob();
    })
    .then(blob => {
      if (!blob) return;
      const a = document.createElement('a');
      const stamp = new Date().toISOString().slice(0, 10);
      a.href = URL.createObjectURL(blob);
      a.download = `trades-${stamp}.csv`;
      document.body.appendChild(a);
      a.click();
      URL.revokeObjectURL(a.href);
      a.remove();
    })
    .catch(err => alert(err.message || '导出失败'));
}

async function bootPanel() {
  initFilters();
  await loadDeviceOptions();
  await loadTrades(1);
}"""

POSITIONS_MAIN = """      <div class="card">
        <div class="filter-actions" style="margin-bottom:12px">
          <button class="secondary" onclick="loadPositions(positionPage)">刷新</button>
        </div>
        <table>
          <thead>
            <tr>
              <th>用户昵称</th><th>联系方式</th><th>黄金持仓</th><th>白银持仓</th><th>黄金委托</th><th>白银委托</th><th>在线</th><th>最后同步</th>
            </tr>
          </thead>
          <tbody id="positionRows"></tbody>
        </table>
        <div class="pager">
          <button class="secondary" id="prevBtn" onclick="loadPositions(positionPage - 1)">上一页</button>
          <span id="pageInfo" class="sub"></span>
          <button class="secondary" id="nextBtn" onclick="loadPositions(positionPage + 1)">下一页</button>
          <label class="pager-size">每页
            <select id="pageSize" onchange="loadPositions(1)">
              <option value="10">10</option>
              <option value="20" selected>20</option>
              <option value="50">50</option>
            </select>
          </label>
        </div>
      </div>"""

POSITIONS_SCRIPT = """let adminToken = '';
let positionPage = 1;

function logout() { logoutAdmin(); }

async function loadPositions(page) {
  const pageSize = document.getElementById('pageSize').value;
  positionPage = Math.max(1, page);
  const url = `/api/v1/admin/positions?page=${positionPage}&page_size=${pageSize}`;
  const res = await fetch(url, { headers: authHeaders(adminToken) });
  if (res.status === 401) return logout();
  const data = await res.json();
  positionPage = data.page || positionPage;
  const tbody = document.getElementById('positionRows');
  tbody.innerHTML = '';
  for (const item of data.positions || []) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${esc(item.display_name || '-')}</td>
      <td>${esc(item.contact || '-')}</td>
      <td>${esc(item.xau_position || '无仓')}</td>
      <td>${esc(item.xag_position || '无仓')}</td>
      <td>${esc(item.xau_open_orders || '无委托')}</td>
      <td>${esc(item.xag_open_orders || '无委托')}</td>
      <td>${onlineDot(item)}</td>
      <td>${esc(fmtBeijing(item.last_seen_at))}</td>`;
    tbody.appendChild(tr);
  }
  const pages = data.pages || 1;
  document.getElementById('pageInfo').textContent = `第 ${positionPage} / ${pages} 页 · 共 ${data.total||0} 条`;
  document.getElementById('prevBtn').disabled = positionPage <= 1;
  document.getElementById('nextBtn').disabled = positionPage >= pages;
}

async function bootPanel() {
  await loadPositions(1);
}"""

AUDIT_MAIN = """      <div class="card filter-bar">
        <label>操作类型
          <select id="actionFilter" onchange="loadAudit(1)">
            <option value="" selected>全部</option>
            <option value="approve">审核通过</option>
            <option value="auto_approve">自动通过</option>
            <option value="reject">拒绝</option>
            <option value="disable">停用</option>
            <option value="delete_device">删除设备</option>
            <option value="update_device">修改设备</option>
            <option value="change_password">修改密码</option>
            <option value="admin_logout">管理员退出</option>
          </select>
        </label>
        <div class="filter-actions">
          <button class="secondary" onclick="loadAudit(auditPage)">刷新</button>
        </div>
      </div>

      <div class="card">
        <table>
          <thead>
            <tr><th>时间</th><th>操作</th><th>目标设备</th><th>详情</th><th>来源 IP</th></tr>
          </thead>
          <tbody id="auditRows"></tbody>
        </table>
        <div class="pager">
          <button class="secondary" id="prevBtn" onclick="loadAudit(auditPage - 1)">上一页</button>
          <span id="pageInfo" class="sub"></span>
          <button class="secondary" id="nextBtn" onclick="loadAudit(auditPage + 1)">下一页</button>
          <label class="pager-size">每页
            <select id="pageSize" onchange="loadAudit(1)">
              <option value="50" selected>50</option>
              <option value="100">100</option>
              <option value="200">200</option>
            </select>
          </label>
        </div>
      </div>"""

AUDIT_SCRIPT = """let adminToken = '';
let auditPage = 1;

const AUDIT_LABELS = {
  approve: '审核通过',
  auto_approve: '自动通过',
  reject: '拒绝',
  disable: '停用',
  delete_device: '删除设备',
  update_device: '修改设备',
  change_password: '修改密码',
  admin_logout: '管理员退出',
};

function auditLabel(a) { return AUDIT_LABELS[a] || a || '-'; }

function logout() { logoutAdmin(); }

async function loadAudit(page) {
  const action = document.getElementById('actionFilter').value;
  const pageSize = document.getElementById('pageSize').value;
  auditPage = Math.max(1, page);
  let url = `/api/v1/admin/audit?page=${auditPage}&page_size=${pageSize}`;
  if (action) url += `&action=${encodeURIComponent(action)}`;
  const res = await fetch(url, { headers: authHeaders(adminToken) });
  if (res.status === 401) return logout();
  const data = await res.json();
  auditPage = data.page || auditPage;
  const tbody = document.getElementById('auditRows');
  tbody.innerHTML = '';
  for (const it of data.items || []) {
    const tr = document.createElement('tr');
    const dev = it.target_device_id ? esc(String(it.target_device_id).slice(0, 12)) + '…' : '-';
    tr.innerHTML = `
      <td>${esc(fmtBeijing(it.at))}</td>
      <td>${esc(auditLabel(it.action))}</td>
      <td><code>${dev}</code></td>
      <td>${esc(it.detail || '-')}</td>
      <td><code>${esc(it.ip || '-')}</code></td>`;
    tbody.appendChild(tr);
  }
  const pages = data.pages || 1;
  document.getElementById('pageInfo').textContent = `第 ${auditPage} / ${pages} 页 · 共 ${data.total||0} 条`;
  document.getElementById('prevBtn').disabled = auditPage <= 1;
  document.getElementById('nextBtn').disabled = auditPage >= pages;
}

async function bootPanel() {
  await loadAudit(1);
}"""

PAGES = [
    (
        "admin.html",
        shell(
            "交易助手 · 授权管理",
            "交易助手 · 授权管理",
            "审核用户申请、停用或删除设备",
            "devices",
            ADMIN_MAIN,
            ADMIN_SCRIPT,
        ),
    ),
    (
        "dashboard.html",
        shell(
            "交易助手 · 数据看板",
            "交易助手 · 数据看板",
            "按北京时间日期聚合的成交净利趋势与活跃用户",
            "dashboard",
            DASHBOARD_MAIN,
            DASHBOARD_SCRIPT,
        ),
    ),
    (
        "trades.html",
        shell(
            "交易助手 · 交易明细",
            "交易助手 · 交易明细",
            "查看各用户上报的开仓与平仓明细（含下单时点差、双端价格、数量与方向）",
            "trades",
            TRADES_MAIN,
            TRADES_SCRIPT,
        ),
    ),
    (
        "positions.html",
        shell(
            "交易助手 · 持仓列表",
            "交易助手 · 持仓列表",
            "各用户最新上报的黄金 / 白银持仓与委托（客户端心跳同步）",
            "positions",
            POSITIONS_MAIN,
            POSITIONS_SCRIPT,
        ),
    ),
    (
        "audit.html",
        shell(
            "交易助手 · 操作日志",
            "交易助手 · 操作日志",
            "记录审核、停用、删除、改密等运营操作，便于追溯",
            "audit",
            AUDIT_MAIN,
            AUDIT_SCRIPT,
        ),
    ),
]


def verify_utf8(path: pathlib.Path, text: str) -> None:
    encoded = text.encode("utf-8")
    decoded = encoded.decode("utf-8")
    if decoded != text:
        raise ValueError(f"{path}: UTF-8 round-trip mismatch")


def main() -> int:
    TEMPLATES.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for filename, content in PAGES:
        path = TEMPLATES / filename
        verify_utf8(path, content)
        path.write_text(content.replace("\r\n", "\n"), encoding="utf-8")
        written.append(str(path))
        print(f"Wrote {path} ({len(content.encode('utf-8'))} bytes UTF-8)")

    print(f"\nOK: patched {len(written)} templates")
    return 0


if __name__ == "__main__":
    sys.exit(main())

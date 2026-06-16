const STATUS_LABELS = {
  pending: '待审核',
  approved: '已通过',
  rejected: '已拒绝',
  disabled: '已停用',
};

const MODE_LABELS = {
  contraction: '收缩',
  expansion: '扩张',
};

const PRESET_LABELS = {
  xau: '黄金',
  xag: '白银',
};

function statusLabel(status) {
  return STATUS_LABELS[status] || status || '-';
}

const ACTION_LABELS = {
  open: '开仓',
  close: '平仓',
};

function actionLabel(action) {
  return ACTION_LABELS[action] || action || '-';
}

function modeLabel(mode) {
  return MODE_LABELS[mode] || mode || '-';
}

function fmtSpread(n) {
  const v = Number(n || 0);
  return (v >= 0 ? '+' : '') + v.toFixed(3);
}

function fmtPrice(n) {
  const v = Number(n || 0);
  return v ? v.toFixed(3) : '-';
}

function fmtQty(n) {
  const v = Number(n || 0);
  return v ? String(v) : '-';
}

function presetLabel(preset) {
  return PRESET_LABELS[preset] || preset || '-';
}

function onlineDot(device) {
  const online = Boolean(device && device.online);
  const cls = online ? 'online-dot online' : 'online-dot';
  const title = online ? '在线' : '离线';
  return `<span class="${cls}" title="${title}" aria-label="${title}"></span>`;
}

function deviceApiPath(deviceId, action = '') {
  const encoded = encodeURIComponent(deviceId);
  if (action === 'update') return `/api/v1/admin/devices/${encoded}/update`;
  if (action) return `/api/v1/admin/devices/${encoded}/${action}`;
  return `/api/v1/admin/devices/${encoded}`;
}

function accountApiPath(deviceId, platform, action) {
  return `/api/v1/admin/devices/${encodeURIComponent(deviceId)}/accounts/${platform}/${action}`;
}

function autoTradeApiPath(deviceId, action) {
  return `/api/v1/admin/devices/${encodeURIComponent(deviceId)}/auto-trade/${action}`;
}

const ACCOUNT_STATUS_LABELS = {
  pending: '待审核',
  enabled: '已启用',
  disabled: '已停用',
};

function accountStatusLabel(status) {
  return ACCOUNT_STATUS_LABELS[status] || status || '待审核';
}

function accountStatusClass(status) {
  if (status === 'enabled') return 'approved';
  if (status === 'disabled') return 'disabled';
  return 'pending';
}

function fmtBeijingShort(iso) {
  if (!iso || iso === '-') return '-';
  const full = fmtBeijing(iso);
  if (full === '-') return '-';
  const parts = full.split(' ');
  if (parts.length < 2) return full;
  const date = parts[0].slice(5);
  const time = parts[1].slice(0, 5);
  return `${date} ${time}`;
}

function shortCode(value, head = 8, tail = 4) {
  const raw = String(value || '').trim();
  if (!raw || raw === '-') return '-';
  if (raw.length <= head + tail + 1) return raw;
  return `${raw.slice(0, head)}…${raw.slice(-tail)}`;
}

function closeAllActionMenus() {
  document.querySelectorAll('.action-menu-panel.open').forEach((el) => {
    el.classList.remove('open');
  });
}

document.addEventListener('click', () => closeAllActionMenus());

function fmtBeijing(iso) {
  if (!iso || iso === '-') return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso).replace('T', ' ').replace('+00:00', '');
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).formatToParts(d);
  const get = (type) => parts.find((p) => p.type === type)?.value || '';
  return `${get('year')}-${get('month')}-${get('day')} ${get('hour')}:${get('minute')}:${get('second')}`;
}

function fmtMoney(n) {
  const v = Number(n || 0);
  return (v >= 0 ? '+' : '') + v.toFixed(2);
}

function fmtTradePnl(action, n) {
  if (action === 'open') return '—';
  return fmtMoney(n);
}

function fmtFundingFee(action, n) {
  if (action === 'open') return '—';
  return fmtMoney(n);
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[c]));
}

async function readError(res) {
  try {
    const data = await res.json();
    const detail = data.detail;
    if (Array.isArray(detail)) {
      return detail.map((item) => item.msg || JSON.stringify(item)).join('；');
    }
    return detail || data.message || '';
  } catch (_) {
    return '';
  }
}

function authHeaders(adminToken) {
  return { Authorization: `Bearer ${adminToken}`, 'Content-Type': 'application/json' };
}

const ADMIN_MODULE_PATHS = {
  devices: '/admin',
  dashboard: '/admin/dashboard',
  trades: '/admin/trades',
  positions: '/admin/positions',
  audit: '/admin/audit',
  roles: '/admin/roles',
  users: '/admin/users',
};

let adminProfile = null;

function showLoginErr(msg) {
  const el = document.getElementById('loginErr');
  if (!el) return;
  el.textContent = msg || '';
  el.classList.toggle('hidden', !msg);
}

function adminCanAccess(moduleKey) {
  if (!adminProfile) return false;
  const mods = adminProfile.modules || [];
  if (mods.includes('*')) return true;
  return mods.includes(moduleKey);
}

const ADMIN_MODULE_ICONS = {
  devices: '授',
  dashboard: '看',
  trades: '单',
  positions: '仓',
  audit: '志',
  roles: '角',
  users: '员',
};

const SIDEBAR_COLLAPSED_KEY = 'ta_admin_sidebar_collapsed';

function isSidebarCollapsed() {
  return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === '1';
}

function setSidebarCollapsed(collapsed) {
  const shell = document.querySelector('.admin-shell');
  if (!shell) return;
  shell.classList.toggle('sidebar-collapsed', collapsed);
  localStorage.setItem(SIDEBAR_COLLAPSED_KEY, collapsed ? '1' : '0');
  const btn = document.getElementById('sidebarToggle');
  if (btn) {
    btn.textContent = collapsed ? '▶' : '◀';
    const label = collapsed ? '展开侧栏' : '收起侧栏';
    btn.title = label;
    btn.setAttribute('aria-label', label);
  }
}

function initAdminSidebar() {
  setSidebarCollapsed(isSidebarCollapsed());
  const btn = document.getElementById('sidebarToggle');
  if (!btn || btn.dataset.wired === '1') return;
  btn.dataset.wired = '1';
  btn.addEventListener('click', (event) => {
    event.stopPropagation();
    setSidebarCollapsed(!isSidebarCollapsed());
  });
}

function showAdminPanel() {
  document.getElementById('loginCard')?.classList.add('hidden');
  document.getElementById('panel')?.classList.remove('hidden');
  document.body.classList.add('admin-logged-in');
  initAdminSidebar();
}

function showLoginCard() {
  // 仅在确认未登录时才显示登录框；默认隐藏可避免整页切换时先闪一下登录框再跳回面板
  document.getElementById('loginCard')?.classList.remove('hidden');
  document.getElementById('panel')?.classList.add('hidden');
  document.body.classList.remove('admin-logged-in');
}

function renderAdminNav(activeModule) {
  const container = document.getElementById('adminNavLinks');
  if (!container || !adminProfile) return;
  const nav = adminProfile.nav || [];
  container.innerHTML = nav.map(({ key, label }) => {
    const href = ADMIN_MODULE_PATHS[key] || '#';
    const active = key === activeModule ? ' active' : '';
    const icon = ADMIN_MODULE_ICONS[key] || label.slice(0, 1);
    return `<a href="${href}" class="sidebar-link${active}" title="${esc(label)}"><span class="sidebar-icon" aria-hidden="true">${esc(icon)}</span><span class="sidebar-label">${esc(label)}</span></a>`;
  }).join('');
  const userEl = document.getElementById('adminUserLabel');
  if (userEl) {
    const name = adminProfile.display_name || adminProfile.username || '';
    const role = adminProfile.role_name || '';
    const text = role ? `${name} · ${role}` : name;
    userEl.textContent = text;
    userEl.title = text;
  }
  initAdminSidebar();
}

async function fetchAdminProfile(token) {
  const res = await fetch('/api/v1/admin/me', { headers: authHeaders(token) });
  if (res.status === 401) return null;
  if (!res.ok) throw new Error(await readError(res) || '加载用户信息失败');
  return res.json();
}

async function doAdminLogin() {
  const username = (document.getElementById('adminUsername')?.value || 'admin').trim();
  const password = document.getElementById('adminPassword')?.value || '';
  if (!username || !password) {
    showLoginErr('请输入用户名和密码');
    return null;
  }
  const res = await fetch('/api/v1/admin/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    showLoginErr(await readError(res) || '用户名或密码错误');
    return null;
  }
  const data = await res.json();
  const token = data.access_token;
  localStorage.setItem('ta_admin_token', token);
  adminProfile = await fetchAdminProfile(token);
  if (!adminProfile) {
    localStorage.removeItem('ta_admin_token');
    showLoginErr('登录失败');
    return null;
  }
  showLoginErr('');
  showAdminPanel();
  return token;
}

async function initAdminPage(activeModule, onReady) {
  let token = localStorage.getItem('ta_admin_token') || '';
  if (token) {
    try {
      adminProfile = await fetchAdminProfile(token);
    } catch (_) {
      adminProfile = null;
    }
    if (!adminProfile) {
      localStorage.removeItem('ta_admin_token');
      token = '';
    } else if (
      activeModule
      && !adminCanAccess(activeModule)
      && !(adminProfile.nav || []).some((item) => item.key === activeModule)
    ) {
      const first = (adminProfile.nav || [])[0];
      const target = first ? ADMIN_MODULE_PATHS[first.key] : '';
      if (target && location.pathname !== target) {
        location.href = target;
        return '';
      }
    }
  }
  if (token && adminProfile) {
    showAdminPanel();
    renderAdminNav(activeModule);
    if (onReady) await onReady(token, adminProfile);
  } else {
    showLoginCard();
  }
  return token;
}

function logoutAdmin() {
  const token = localStorage.getItem('ta_admin_token') || '';
  serverLogout(token);
}

function wireAdminLoginButton(onSuccess) {
  const btn = document.getElementById('loginBtn');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    const token = await doAdminLogin();
    if (!token) return;
    renderAdminNav(null);
    if (onSuccess) await onSuccess(token);
  });
  const pwd = document.getElementById('adminPassword');
  if (pwd) {
    pwd.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') btn.click();
    });
  }
}

async function serverLogout(adminToken) {
  // 通知服务端吊销令牌版本，使本次会话令牌立即失效，再清本地
  try {
    await fetch('/api/v1/admin/logout', { method: 'POST', headers: authHeaders(adminToken) });
  } catch (_) {
    /* 网络失败也继续本地登出 */
  }
  localStorage.removeItem('ta_admin_token');
  location.reload();
}

function beijingTodayDate() {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date());
  const get = (type) => parts.find((p) => p.type === type)?.value || '';
  return `${get('year')}-${get('month')}-${get('day')}`;
}

function beijingDateDaysAgo(days) {
  const d = new Date(Date.now() - days * 86400000);
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(d);
  const get = (type) => parts.find((p) => p.type === type)?.value || '';
  return `${get('year')}-${get('month')}-${get('day')}`;
}

function setTradeDateInputValue(inputId, day) {
  const el = document.getElementById(inputId);
  if (el) el.value = day ? String(day).trim().slice(0, 10) : '';
}

function wireNativePickerInput(el) {
  if (!el) return;
  const type = el.type;
  if (type !== 'date' && type !== 'datetime-local') return;
  el.classList.add(type === 'date' ? 'date-input' : 'datetime-input');
  const openPicker = () => {
    if (el.disabled) return;
    if (typeof el.showPicker === 'function') {
      try {
        el.showPicker();
      } catch (_) {
        /* 部分浏览器需在用户手势内调用 */
      }
    }
  };
  el.addEventListener('click', openPicker);
}

function wireNativeDateInput(el) {
  wireNativePickerInput(el);
}

function initTradeDateFilters(onChange) {
  const today = beijingTodayDate();
  setTradeDateInputValue('dateFrom', beijingDateDaysAgo(7));
  setTradeDateInputValue('dateTo', today);
  for (const id of ['dateFrom', 'dateTo']) {
    const el = document.getElementById(id);
    if (!el) continue;
    wireNativeDateInput(el);
    if (onChange) el.addEventListener('change', onChange);
  }
}

function resetTradeDateDefaults() {
  const today = beijingTodayDate();
  setTradeDateInputValue('dateFrom', beijingDateDaysAgo(7));
  setTradeDateInputValue('dateTo', today);
}

function getTradeDateFilterValue(inputId, bound) {
  const el = document.getElementById(inputId);
  if (!el || !el.value) return '';
  const day = el.value;
  if (bound === 'end') return `${day} 23:59:59`;
  return `${day} 00:00:00`;
}

function toDatetimeLocalValue(apiValue) {
  if (!apiValue) return '';
  const raw = String(apiValue).trim();
  if (/[Tt].*[Zz+]/.test(raw) || raw.endsWith('Z')) {
    return isoToDatetimeLocal(raw);
  }
  return raw.replace(' ', 'T').slice(0, 19);
}

function isoToDatetimeLocal(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return toDatetimeLocalValue(iso);
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).formatToParts(d);
  const get = (type) => parts.find((p) => p.type === type)?.value || '';
  return `${get('year')}-${get('month')}-${get('day')}T${get('hour')}:${get('minute')}:${get('second')}`;
}

function beijingDatetimeLocalAfterDays(days) {
  return isoToDatetimeLocal(new Date(Date.now() + days * 86400000).toISOString());
}

function openDisplayNameDialog(options = {}) {
  const { title = '修改昵称', currentValue = '' } = options;

  return new Promise((resolve) => {
    const backdrop = document.createElement('div');
    backdrop.className = 'modal-backdrop';

    const card = document.createElement('div');
    card.className = 'modal-card';
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-modal', 'true');

    const heading = document.createElement('h2');
    heading.textContent = title;

    const hint = document.createElement('p');
    hint.className = 'sub modal-hint';
    hint.textContent = '昵称会显示在授权列表和交易明细中。';

    const field = document.createElement('label');
    field.className = 'modal-field';
    field.textContent = '用户昵称';
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'modal-text';
    input.maxLength = 64;
    input.placeholder = '请输入昵称';
    input.value = currentValue;
    field.appendChild(input);

    const actions = document.createElement('div');
    actions.className = 'modal-actions';
    const cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'secondary';
    cancelBtn.textContent = '取消';
    const okBtn = document.createElement('button');
    okBtn.type = 'button';
    okBtn.textContent = '保存';

    const close = (value) => {
      backdrop.remove();
      document.removeEventListener('keydown', onKeyDown);
      resolve(value);
    };

    const onKeyDown = (event) => {
      if (event.key === 'Escape') close(null);
      if (event.key === 'Enter') okBtn.click();
    };

    cancelBtn.addEventListener('click', () => close(null));
    okBtn.addEventListener('click', () => {
      const trimmed = input.value.trim();
      if (!trimmed) {
        alert('昵称不能为空');
        input.focus();
        return;
      }
      close(trimmed);
    });

    backdrop.addEventListener('click', (event) => {
      if (event.target === backdrop) close(null);
    });

    actions.append(cancelBtn, okBtn);
    card.append(heading, hint, field, actions);
    backdrop.appendChild(card);
    document.body.appendChild(backdrop);
    document.addEventListener('keydown', onKeyDown);
    input.focus();
    input.select();
  });
}

function openExpiresAtDialog(options = {}) {
  const {
    title = '设置授权到期时间',
    currentValue = '',
    defaultPermanent = !currentValue,
  } = options;

  return new Promise((resolve) => {
    const backdrop = document.createElement('div');
    backdrop.className = 'modal-backdrop';

    const card = document.createElement('div');
    card.className = 'modal-card';
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-modal', 'true');

    const heading = document.createElement('h2');
    heading.textContent = title;

    const hint = document.createElement('p');
    hint.className = 'sub modal-hint';
    hint.textContent = '时间为北京时间。勾选「永久授权」则不限制到期。';

    const permanentRow = document.createElement('label');
    permanentRow.className = 'modal-check';
    const permanentInput = document.createElement('input');
    permanentInput.type = 'checkbox';
    permanentInput.checked = defaultPermanent;
    permanentRow.appendChild(permanentInput);
    permanentRow.append(' 永久授权');

    const dateRow = document.createElement('label');
    dateRow.className = 'modal-field';
    dateRow.textContent = '到期时间';
    const dateInput = document.createElement('input');
    dateInput.type = 'datetime-local';
    dateInput.step = '1';
    dateInput.className = 'dt-input modal-datetime';
    dateInput.value = toDatetimeLocalValue(currentValue);
    dateRow.appendChild(dateInput);

    const presets = document.createElement('div');
    presets.className = 'modal-presets';
    const presetDefs = [
      ['30 天', 30],
      ['90 天', 90],
      ['1 年', 365],
    ];
    for (const [label, days] of presetDefs) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'secondary';
      btn.textContent = label;
      btn.addEventListener('click', () => {
        permanentInput.checked = false;
        dateInput.disabled = false;
        dateInput.value = beijingDatetimeLocalAfterDays(days);
        dateInput.focus();
      });
      presets.appendChild(btn);
    }

    const syncPermanent = () => {
      dateInput.disabled = permanentInput.checked;
      if (permanentInput.checked) {
        dateInput.value = '';
      } else if (!dateInput.value) {
        dateInput.value = beijingDatetimeLocalAfterDays(30);
      }
    };
    permanentInput.addEventListener('change', syncPermanent);
    syncPermanent();
    wireNativePickerInput(dateInput);

    const actions = document.createElement('div');
    actions.className = 'modal-actions';
    const cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'secondary';
    cancelBtn.textContent = '取消';
    const okBtn = document.createElement('button');
    okBtn.type = 'button';
    okBtn.textContent = '确定';

    const close = (value) => {
      backdrop.remove();
      document.removeEventListener('keydown', onKeyDown);
      resolve(value);
    };

    const onKeyDown = (event) => {
      if (event.key === 'Escape') close(null);
    };

    cancelBtn.addEventListener('click', () => close(null));
    okBtn.addEventListener('click', () => {
      if (permanentInput.checked) {
        close('');
        return;
      }
      const apiValue = fromDatetimeLocalValue(dateInput.value);
      if (!apiValue) {
        alert('请选择到期时间，或勾选永久授权');
        dateInput.focus();
        return;
      }
      close(apiValue);
    });

    backdrop.addEventListener('click', (event) => {
      if (event.target === backdrop) close(null);
    });

    actions.append(cancelBtn, okBtn);
    card.append(heading, hint, permanentRow, dateRow, presets, actions);
    backdrop.appendChild(card);
    document.body.appendChild(backdrop);
    document.addEventListener('keydown', onKeyDown);
    (permanentInput.checked ? permanentInput : dateInput).focus();
  });
}

function fromDatetimeLocalValue(localValue) {
  const value = String(localValue || '').trim();
  if (!value) return '';
  if (value.length === 16) return `${value.replace('T', ' ')}:00`;
  return value.replace('T', ' ');
}

function setDateInputValue(inputId, apiValue) {
  const el = document.getElementById(inputId);
  if (el) el.value = toDatetimeLocalValue(apiValue);
}

async function verifyAdminPassword(adminToken, password) {
  const res = await fetch('/api/v1/admin/verify-password', {
    method: 'POST',
    headers: authHeaders(adminToken),
    body: JSON.stringify({ password }),
  });
  if (res.status === 401) {
    alert('登录已过期，请重新登录');
    localStorage.removeItem('ta_admin_token');
    location.reload();
    return false;
  }
  if (!res.ok) {
    alert(await readError(res) || '当前密码错误');
    return false;
  }
  return true;
}

function _pwField(labelText, placeholder) {
  const field = document.createElement('label');
  field.className = 'modal-field';
  field.textContent = labelText;
  const input = document.createElement('input');
  input.type = 'password';
  input.className = 'modal-text';
  input.maxLength = 128;
  input.placeholder = placeholder;
  input.autocomplete = 'new-password';
  field.appendChild(input);
  return { field, input };
}

function openChangePasswordDialog(adminToken) {
  return new Promise((resolve) => {
    const backdrop = document.createElement('div');
    backdrop.className = 'modal-backdrop';

    const card = document.createElement('div');
    card.className = 'modal-card';
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-modal', 'true');

    const heading = document.createElement('h2');
    heading.textContent = '修改管理员密码';

    const hint = document.createElement('p');
    hint.className = 'sub modal-hint';
    hint.textContent = '新密码至少 12 位。修改成功后需用新密码重新登录。';

    const oldF = _pwField('当前密码', '请输入当前密码');
    const newF = _pwField('新密码', '至少 12 位');
    const confirmF = _pwField('确认新密码', '再次输入新密码');

    const err = document.createElement('p');
    err.className = 'err modal-hint';
    err.style.display = 'none';

    const actions = document.createElement('div');
    actions.className = 'modal-actions';
    const cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'secondary';
    cancelBtn.textContent = '取消';
    const okBtn = document.createElement('button');
    okBtn.type = 'button';
    okBtn.textContent = '确认修改';

    const close = (value) => {
      backdrop.remove();
      document.removeEventListener('keydown', onKeyDown);
      resolve(value);
    };
    const onKeyDown = (event) => {
      if (event.key === 'Escape') close(false);
      if (event.key === 'Enter') okBtn.click();
    };
    const showErr = (msg) => {
      err.textContent = msg;
      err.style.display = msg ? 'block' : 'none';
    };

    cancelBtn.addEventListener('click', () => close(false));
    okBtn.addEventListener('click', async () => {
      const oldPwd = oldF.input.value;
      const newPwd = newF.input.value;
      const confirmPwd = confirmF.input.value;
      if (!oldPwd) return showErr('请输入当前密码');
      if (newPwd.length < 12) return showErr('新密码至少 12 位');
      if (newPwd !== confirmPwd) return showErr('两次输入的新密码不一致');
      if (newPwd === oldPwd) return showErr('新密码不能与当前密码相同');
      okBtn.disabled = true;
      showErr('');
      const res = await fetch('/api/v1/admin/change-password', {
        method: 'POST',
        headers: authHeaders(adminToken),
        body: JSON.stringify({ old_password: oldPwd, new_password: newPwd }),
      });
      if (res.status === 401) {
        alert('登录已过期，请重新登录');
        localStorage.removeItem('ta_admin_token');
        location.reload();
        return;
      }
      if (!res.ok) {
        okBtn.disabled = false;
        showErr(await readError(res) || '修改失败');
        return;
      }
      close(true);
      alert('密码已更新，请使用新密码重新登录');
      localStorage.removeItem('ta_admin_token');
      location.reload();
    });

    backdrop.addEventListener('click', (event) => {
      if (event.target === backdrop) close(false);
    });

    actions.append(cancelBtn, okBtn);
    card.append(heading, hint, oldF.field, newF.field, confirmF.field, err, actions);
    backdrop.appendChild(card);
    document.body.appendChild(backdrop);
    document.addEventListener('keydown', onKeyDown);
    oldF.input.focus();
  });
}

const LIST_COLUMNS_STORAGE_PREFIX = 'ta_admin_list_columns:v1:';

function defaultListColumnState(defs) {
  const order = [];
  const visible = {};
  for (const def of defs) {
    order.push(def.key);
    visible[def.key] = def.defaultVisible !== false;
  }
  return { order, visible };
}

function loadListColumnState(listId, defs) {
  const fallback = defaultListColumnState(defs);
  try {
    const raw = localStorage.getItem(`${LIST_COLUMNS_STORAGE_PREFIX}${listId}`);
    if (!raw) return fallback;
    const saved = JSON.parse(raw);
    const known = new Set(defs.map((d) => d.key));
    const order = [];
    for (const key of saved.order || []) {
      if (known.has(key) && !order.includes(key)) order.push(key);
    }
    for (const def of defs) {
      if (!order.includes(def.key)) order.push(def.key);
    }
    const visible = { ...fallback.visible };
    for (const [key, value] of Object.entries(saved.visible || {})) {
      if (known.has(key)) visible[key] = Boolean(value);
    }
    for (const def of defs) {
      if (def.locked) visible[def.key] = true;
    }
    return { order, visible };
  } catch (_) {
    return fallback;
  }
}

function saveListColumnState(listId, state) {
  localStorage.setItem(`${LIST_COLUMNS_STORAGE_PREFIX}${listId}`, JSON.stringify(state));
}

function resetListColumnState(listId) {
  localStorage.removeItem(`${LIST_COLUMNS_STORAGE_PREFIX}${listId}`);
}

function getActiveListColumns(listId, defs) {
  const state = loadListColumnState(listId, defs);
  const defMap = Object.fromEntries(defs.map((d) => [d.key, d]));
  const unlocked = [];
  const locked = [];
  for (const key of state.order) {
    const def = defMap[key];
    if (!def) continue;
    if (def.locked) {
      locked.push(def);
      continue;
    }
    if (state.visible[key] === false) continue;
    unlocked.push(def);
  }
  for (const def of defs) {
    if (!def.locked) continue;
    if (!locked.some((item) => item.key === def.key)) locked.push(def);
  }
  return [...unlocked, ...locked];
}

function renderListTableHeader(tableEl, listId, defs) {
  const cols = getActiveListColumns(listId, defs);
  const tr = tableEl.querySelector('thead tr');
  if (tr) {
    tr.innerHTML = cols.map((c) => `<th data-col="${esc(c.key)}">${esc(c.label)}</th>`).join('');
  }
  return cols;
}

function renderListRows(tbody, rows, cols, renderCell, options = {}) {
  const { emptyMessage = '', onRow = null } = options;
  tbody.innerHTML = '';
  if (!rows.length) {
    if (emptyMessage) {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td colspan="${Math.max(1, cols.length)}" class="sub" style="text-align:center;padding:24px">${emptyMessage}</td>`;
      tbody.appendChild(tr);
    }
    return;
  }
  for (const row of rows) {
    const tr = document.createElement('tr');
    tr.innerHTML = cols.map((col) => {
      const html = renderCell(col.key, row);
      return `<td data-col="${esc(col.key)}">${html == null ? '' : html}</td>`;
    }).join('');
    if (onRow) onRow(tr, row);
    tbody.appendChild(tr);
  }
}

function applyListTable({ listId, defs, tableEl, rows, renderCell, emptyMessage, onRow }) {
  const cols = renderListTableHeader(tableEl, listId, defs);
  const tbody = tableEl.querySelector('tbody');
  renderListRows(tbody, rows, cols, renderCell, { emptyMessage, onRow });
  return cols;
}

function mountListColumnControl(anchorEl, listId, defs, onApply) {
  if (!anchorEl || anchorEl.dataset.columnMounted === '1') return;
  anchorEl.dataset.columnMounted = '1';
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'secondary';
  btn.textContent = '自定义列';
  btn.addEventListener('click', () => openListColumnDialog(listId, defs, onApply));
  anchorEl.appendChild(btn);
}

function openListColumnDialog(listId, defs, onApply) {
  const state = loadListColumnState(listId, defs);
  const sortable = defs.filter((d) => !d.locked);
  const locked = defs.filter((d) => d.locked);
  const orderedSortable = [];
  for (const key of state.order) {
    const def = sortable.find((d) => d.key === key);
    if (def && !orderedSortable.some((d) => d.key === key)) orderedSortable.push(def);
  }
  for (const def of sortable) {
    if (!orderedSortable.some((d) => d.key === def.key)) orderedSortable.push(def);
  }

  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop';

  const card = document.createElement('div');
  card.className = 'modal-card column-picker-card';
  card.setAttribute('role', 'dialog');
  card.setAttribute('aria-modal', 'true');

  const heading = document.createElement('h2');
  heading.textContent = '自定义列表字段';

  const hint = document.createElement('p');
  hint.className = 'sub modal-hint';
  hint.textContent = '勾选要显示的字段，拖拽左侧手柄调整列顺序。固定列（如操作）始终显示在末尾。';

  const list = document.createElement('ul');
  list.className = 'column-sort-list';

  let dragKey = '';

  for (const def of orderedSortable) {
    const li = document.createElement('li');
    li.className = 'column-sort-item';
    li.dataset.key = def.key;

    const handle = document.createElement('span');
    handle.className = 'column-drag-handle';
    handle.textContent = '⋮⋮';
    handle.title = '拖拽排序';
    handle.draggable = true;

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = state.visible[def.key] !== false;
    checkbox.addEventListener('change', () => {
      state.visible[def.key] = checkbox.checked;
    });
    checkbox.addEventListener('mousedown', (event) => event.stopPropagation());
    checkbox.addEventListener('click', (event) => event.stopPropagation());

    const label = document.createElement('span');
    label.className = 'column-sort-label';
    label.textContent = def.label;

    li.append(handle, checkbox, label);

    handle.addEventListener('dragstart', (event) => {
      dragKey = def.key;
      li.classList.add('dragging');
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', def.key);
      event.stopPropagation();
    });
    handle.addEventListener('dragend', () => {
      dragKey = '';
      li.classList.remove('dragging');
      list.querySelectorAll('.column-sort-item').forEach((item) => item.classList.remove('drag-over'));
    });
    li.addEventListener('dragover', (event) => {
      event.preventDefault();
      if (!dragKey || dragKey === def.key) return;
      li.classList.add('drag-over');
    });
    li.addEventListener('dragleave', () => li.classList.remove('drag-over'));
    li.addEventListener('drop', (event) => {
      event.preventDefault();
      li.classList.remove('drag-over');
      if (!dragKey || dragKey === def.key) return;
      const keys = [...list.querySelectorAll('.column-sort-item')].map((item) => item.dataset.key);
      const from = keys.indexOf(dragKey);
      const to = keys.indexOf(def.key);
      if (from < 0 || to < 0) return;
      keys.splice(from, 1);
      keys.splice(to, 0, dragKey);
      const map = Object.fromEntries([...list.children].map((item) => [item.dataset.key, item]));
      for (const key of keys) {
        if (map[key]) list.appendChild(map[key]);
      }
    });

    list.appendChild(li);
  }

  if (locked.length) {
    const lockedBox = document.createElement('div');
    lockedBox.className = 'column-locked-box';
    lockedBox.innerHTML = `<div class="sub">固定列</div>${locked.map((d) => `<div class="column-locked-item">${esc(d.label)}</div>`).join('')}`;
    card.append(heading, hint, list, lockedBox);
  } else {
    card.append(heading, hint, list);
  }

  const actions = document.createElement('div');
  actions.className = 'modal-actions';
  const resetBtn = document.createElement('button');
  resetBtn.type = 'button';
  resetBtn.className = 'secondary';
  resetBtn.textContent = '恢复默认';
  const cancelBtn = document.createElement('button');
  cancelBtn.type = 'button';
  cancelBtn.className = 'secondary';
  cancelBtn.textContent = '取消';
  const okBtn = document.createElement('button');
  okBtn.type = 'button';
  okBtn.textContent = '应用';

  const close = () => backdrop.remove();

  resetBtn.addEventListener('click', () => {
    resetListColumnState(listId);
    close();
    if (onApply) onApply();
  });
  cancelBtn.addEventListener('click', close);
  okBtn.addEventListener('click', () => {
    const orderedKeys = [...list.querySelectorAll('.column-sort-item')].map((item) => item.dataset.key);
    for (const def of locked) {
      if (!orderedKeys.includes(def.key)) orderedKeys.push(def.key);
    }
    for (const def of defs) {
      if (!orderedKeys.includes(def.key)) orderedKeys.push(def.key);
    }
    state.order = orderedKeys;
    for (const def of locked) state.visible[def.key] = true;
    saveListColumnState(listId, state);
    close();
    if (onApply) onApply();
  });

  backdrop.addEventListener('click', (event) => {
    if (event.target === backdrop) close();
  });

  actions.append(resetBtn, cancelBtn, okBtn);
  card.appendChild(actions);
  backdrop.appendChild(card);
  document.body.appendChild(backdrop);
}

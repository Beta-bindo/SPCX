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

async function openChangePasswordDialog(adminToken) {
  const oldPwd = prompt('请输入当前密码');
  if (oldPwd === null || !oldPwd) return;

  const verified = await verifyAdminPassword(adminToken, oldPwd);
  if (!verified) return;

  const newPwd = prompt('请输入新密码（至少 12 位）');
  if (newPwd === null) return;
  if (newPwd.length < 12) {
    alert('新密码至少 12 位');
    return;
  }

  const confirmPwd = prompt('请再次输入新密码');
  if (confirmPwd === null) return;
  if (newPwd !== confirmPwd) {
    alert('两次输入的新密码不一致');
    return;
  }

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
    alert(await readError(res) || '修改失败');
    return;
  }

  alert('密码已更新，请使用新密码重新登录');
  localStorage.removeItem('ta_admin_token');
  location.reload();
}

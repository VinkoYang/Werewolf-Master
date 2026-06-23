// app.js – Socket.IO client for Moon Verdict
'use strict';

// ── State ────────────────────────────────────────────────────────────────────
let myNick = '';
let inRoom = false;
let lobbyData = null;         // last lobby payload
let lastCountdownKey = null;  // prevent restarting the same countdown
let countdownTimer = null;
let currentRoomConfig = '';   // latest room config text for '查看配置'
let _reconfigureMode = false; // true when custom modal is in reconfigure-room mode

// ── Socket.IO ────────────────────────────────────────────────────────────────
const token = localStorage.getItem('mv_token') || '';
const socket = io({ auth: { token }, reconnection: true, reconnectionDelay: 1000 });

socket.on('connect', () => {
  console.log('connected', socket.id);
  setStatus('已连接');
});

socket.on('disconnect', () => {
  setStatus('连接断开，正在重连…');
});

socket.on('connect_error', (err) => {
  setStatus('连接失败：' + err.message);
});

// ── Auth events ───────────────────────────────────────────────────────────────
socket.on('login_ok', (data) => {
  myNick = data.nick;
  localStorage.setItem('mv_token', data.token);
  showScreen('lobby-screen');
  setStatus(`你好，${myNick}`);
});

socket.on('login_error', (data) => {
  document.getElementById('login-error').textContent = data.message;
});

// ── Lobby ─────────────────────────────────────────────────────────────────────
socket.on('lobby', (data) => {
  lobbyData = data;
  renderLobby(data);
});

function renderLobby(data) {
  // Room list
  const rl = document.getElementById('room-list');
  if (!data.rooms || data.rooms.length === 0) {
    rl.innerHTML = '<p style="color:#888">暂无可加入的房间</p>';
  } else {
    rl.innerHTML = '';
    data.rooms.forEach(r => {
      const btn = document.createElement('button');
      btn.className = 'btn btn-light';
      btn.style.cssText = 'display:block;width:100%;text-align:left;margin:4px 0;';
      btn.textContent = r.text;
      btn.onclick = () => quickJoin(r.room_id);
      rl.appendChild(btn);
    });
  }

  // Lobby links
  const ll = document.getElementById('lobby-links');
  ll.innerHTML = '';
  if (data.game_resource_links) {
    ll.appendChild(makeLinkSection('游戏资料', data.game_resource_links));
  }
  if (data.guide_links) {
    ll.appendChild(makeLinkSection('攻略 & 新手指南', data.guide_links));
  }
  if (data.dev_links) {
    ll.appendChild(makeLinkSection('开发者信息', data.dev_links));
  }
  if (data.feedback_link) {
    const div = document.createElement('div');
    div.className = 'card link-section';
    div.innerHTML = `<h3>反馈</h3><a href="${data.feedback_link[1]}" target="_blank">${data.feedback_link[0]}</a>`;
    ll.appendChild(div);
  }

  // Preset sections (for create modal)
  if (data.creation_sections) {
    const pg = document.getElementById('preset-sections');
    pg.innerHTML = '';
    data.creation_sections.forEach(([title, btns]) => {
      const h = document.createElement('h4');
      h.textContent = title;
      h.style.margin = '10px 0 6px';
      pg.appendChild(h);
      const row = document.createElement('div');
      row.className = 'preset-grid';
      row.style.cssText = 'display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;';
      btns.forEach(b => {
        const btn = document.createElement('button');
        const colorClass = b.color ? `btn-${b.color}` : 'btn-light';
        btn.className = `btn ${colorClass}`;
        btn.textContent = b.label;
        btn.onclick = () => selectPreset(b.value);
        row.appendChild(btn);
      });
      pg.appendChild(row);
    });
  }
}

function makeLinkSection(title, links) {
  const div = document.createElement('div');
  div.className = 'card link-section';
  div.innerHTML = `<h3>${title}</h3>`;
  links.forEach(([label, url]) => {
    const a = document.createElement('a');
    a.href = url; a.target = '_blank'; a.textContent = label;
    div.appendChild(a);
  });
  return div;
}

// ── Game state ────────────────────────────────────────────────────────────────
socket.on('state', (state) => {
  if (state.in_room) {
    if (!inRoom) {
      inRoom = true;
      showScreen('room-screen');
    }
    if (state.room_config) currentRoomConfig = state.room_config;
    renderRoomState(state);
  } else {
    if (inRoom) {
      inRoom = false;
      stopCountdown();
      showScreen('lobby-screen');
    }
    refreshLobby();
  }
});

socket.on('messages', (msgs) => {
  msgs.forEach(appendMessage);
});

socket.on('countdown_tick', (data) => {
  // Only use server tick for display if the client-side countdown hasn't started yet
  if (!countdownTimer) {
    updateCountdownDisplay(data.seconds);
  }
});

socket.on('countdown_clear', () => {
  stopCountdown();
  document.getElementById('countdown-bar').textContent = '—';
});

socket.on('error', (data) => {
  showToast(data.message || '操作失败', 3000);
});

socket.on('configure_room_modal', (config) => {
  showReconfigureModal(config);
});

// ── Room rendering ────────────────────────────────────────────────────────────
function renderRoomState(state) {
  document.getElementById('room-title').textContent =
    `房间 ${state.room_id} | ${state.seat ? state.seat + '号' : '—'} ${myNick}` +
    (state.role_name ? ` | ${state.role_name}` : '');

  const stageMap = {
    SHERIFF: '警长竞选', SPEECH: '警长发言', NIGHT: '夜晚', WOLF: '狼人行动',
    SEER: '预言家', WITCH: '女巫', GUARD: '守卫', HUNTER: '猎人',
    NIGHTMARE: '梦魇', WOLF_BEAUTY: '狼美人', DREAMER: '摄梦人',
    HALF_BLOOD: '混血儿', WOLF_KING: '狼王', NINE_TAILED_FOX: '九尾妖狐',
    LAST_WORDS: '遗言', EXILE_SPEECH: '放逐发言', EXILE_PK_SPEECH: '放逐PK发言',
    EXILE_VOTE: '放逐投票', EXILE_PK_VOTE: '放逐PK票', BADGE_TRANSFER: '警徽移交',
  };
  document.getElementById('stage-label').textContent =
    state.game_over ? '游戏结束' :
    (state.stage ? (stageMap[state.stage] || state.stage) : (state.started ? '进行中' : '等待开始'));
  document.getElementById('role-label').textContent =
    state.role_name ? `[${state.role_name}]` : '';

  document.getElementById('leave-btn').style.display = (state.started && !state.game_over) ? 'none' : '';

  renderSeatPanel(state);
  renderActions(state.actions || []);

  // Countdown from state
  if (state.countdown && state.countdown.key && state.countdown.key !== lastCountdownKey) {
    lastCountdownKey = state.countdown.key;
    startClientCountdown(state.countdown.seconds, state.countdown.label);
  } else if (!state.countdown) {
    // No countdown active
    if (lastCountdownKey) {
      lastCountdownKey = null;
      stopCountdown();
    }
  }
}

function renderSeatPanel(state) {
  const panel = document.getElementById('seat-panel');
  const snap = state.seat_panel;
  if (!snap || !snap.seats || snap.seats.length === 0) {
    panel.innerHTML = '<p style="color:#888">暂无座位</p>';
    return;
  }
  const ROW = 4;
  let html = '<table id="seat-table"><tbody>';
  let row = [];
  snap.seats.forEach(info => {
    const occupied = info.nick;
    const isMe = (info.nick === myNick);
    let cell;
    if (occupied) {
      if (isMe) {
        cell = `<td><button class="btn btn-primary" disabled>${info.seat}号：${info.nick}</button></td>`;
      } else {
        cell = `<td><button class="btn btn-secondary" disabled>${info.seat}号：${info.nick}</button></td>`;
      }
    } else if (!state.started) {
      cell = `<td><button class="btn btn-success" onclick="selectSeat(${info.seat})">${info.seat}号：空</button></td>`;
    } else {
      cell = `<td><button class="btn btn-secondary" disabled>${info.seat}号：空</button></td>`;
    }
    row.push(cell);
    if (row.length === ROW) {
      html += '<tr>' + row.join('') + '</tr>';
      row = [];
    }
  });
  if (row.length) {
    while (row.length < ROW) row.push('<td></td>');
    html += '<tr>' + row.join('') + '</tr>';
  }
  const standing = snap.standing || [];
  html += '</tbody></table>';
  if (standing.length) {
    html += `<p style="margin-top:6px;color:#888;font-size:0.85rem;">未坐下：${standing.join('、')}</p>`;
  }
  panel.innerHTML = html;
}

function renderActions(actions) {
  const panel = document.getElementById('actions-panel');
  panel.innerHTML = '';
  if (!actions || actions.length === 0) return;

  actions.forEach(action => {
    if (action.type === 'actions') {
      const group = document.createElement('div');
      group.className = 'action-group';
      if (action.help_text) {
        const help = document.createElement('div');
        help.className = 'action-help';
        help.textContent = action.help_text;
        group.appendChild(help);
      }
      (action.buttons || []).forEach(btn => {
        const button = document.createElement('button');
        const color = btn.color || 'primary';
        button.className = `btn btn-${color}`;
        button.textContent = btn.label || btn.value || '';
        button.disabled = !!btn.disabled;
        if (!btn.disabled) {
          button.onclick = () => sendAction(action.name, btn.value);
        }
        group.appendChild(button);
      });
      panel.appendChild(group);
    }
  });
}

// ── Messages ─────────────────────────────────────────────────────────────────
function appendMessage(msg) {
  const box = document.getElementById('msg-box');
  if (!box) return;
  const div = document.createElement('div');
  if (msg.type === 'private') {
    div.className = 'msg-private';
    div.textContent = `▶ ${msg.text}`;
  } else if (msg.type === 'public') {
    div.className = 'msg-public';
    div.textContent = msg.text;
    if (msg.tts && 'speechSynthesis' in window) {
      try {
        const utter = new SpeechSynthesisUtterance(msg.text);
        utter.rate = 1.2;
        window.speechSynthesis.cancel();
        window.speechSynthesis.speak(utter);
      } catch (_) {}
    }
  } else if (msg.type === 'cancel_input') {
    return; // no display needed, actions panel already updates
  } else {
    return;
  }
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

// ── Countdown ─────────────────────────────────────────────────────────────────
function startClientCountdown(seconds, label) {
  stopCountdown();
  let remaining = seconds;
  updateCountdownDisplay(remaining, label);
  countdownTimer = setInterval(() => {
    remaining -= 1;
    if (remaining <= 0) {
      stopCountdown();
      document.getElementById('countdown-bar').textContent = label ? `${label}：已结束` : '已结束';
    } else {
      updateCountdownDisplay(remaining, label);
    }
  }, 1000);
}

function updateCountdownDisplay(seconds, label) {
  const bar = document.getElementById('countdown-bar');
  if (bar) bar.textContent = `${label || '倒计时'}：${seconds}s`;
}

function stopCountdown() {
  if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null; }
}

// ── Socket actions ────────────────────────────────────────────────────────────
function sendAction(name, value) {
  socket.emit('player_action', { [name]: value });
}

function selectSeat(seatNo) {
  socket.emit('select_seat', { seat: seatNo });
}

function doLeaveRoom() {
  socket.emit('leave_room', {});
}

function refreshLobby() {
  socket.emit('get_lobby', {});
}

// ── Login / Logout ────────────────────────────────────────────────────────────
function doLogin() {
  const nick = document.getElementById('nick-input').value.trim();
  if (!nick) return;
  document.getElementById('login-error').textContent = '';
  socket.emit('login', { nick });
}

function doLogout() {
  socket.emit('logout', {});
  myNick = '';
  localStorage.removeItem('mv_token');
  location.reload();
}

document.getElementById('nick-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') doLogin();
});

// ── Room config view ─────────────────────────────────────────────────────────
function viewConfig() {
  if (!currentRoomConfig) return;
  appendMessage({ type: 'private', text: '当前配置：' + currentRoomConfig });
}

function showReconfigureModal(config) {
  _reconfigureMode = true;
  if (lobbyData) populateCustomModal(lobbyData);
  // Pre-fill form with current config
  document.getElementById('c-wolf').value = config.wolf_num ?? 0;
  document.getElementById('c-citizen').value = config.citizen_num ?? 0;
  const godWolf = new Set(config.god_wolf || []);
  document.querySelectorAll('input[name="god_wolf"]').forEach(cb => {
    cb.checked = godWolf.has(cb.value);
  });
  const godCitizen = new Set(config.god_citizen || []);
  document.querySelectorAll('input[name="god_citizen"]').forEach(cb => {
    cb.checked = godCitizen.has(cb.value);
  });
  if (config.witch_rule) document.getElementById('c-witch-rule').value = config.witch_rule;
  if (config.guard_rule) document.getElementById('c-guard-rule').value = config.guard_rule;
  if (config.sheriff_bomb_rule) document.getElementById('c-bomb-rule').value = config.sheriff_bomb_rule;
  document.getElementById('custom-modal-title').textContent = '修改房间配置';
  document.getElementById('custom-modal-submit').textContent = '保存配置';
  document.getElementById('custom-modal').style.display = 'block';
}

// ── Room creation ─────────────────────────────────────────────────────────────
let _customConfig = null;

function showCreateModal() {
  refreshLobby(); // ensure preset list is up to date
  document.getElementById('create-modal').style.display = 'block';
}
function hideCreateModal() {
  document.getElementById('create-modal').style.display = 'none';
}

function selectPreset(presetValue) {
  hideCreateModal();
  if (presetValue === 'preset_custom') {
    showCustomModal();
    return;
  }
  socket.emit('create_room', { preset: presetValue });
}

function showCustomModal() {
  // Populate select options (sent once with lobby data)
  if (lobbyData) {
    populateCustomModal(lobbyData);
  }
  document.getElementById('custom-modal').style.display = 'block';
}
function hideCustomModal() {
  document.getElementById('custom-modal').style.display = 'none';
  if (_reconfigureMode) {
    _reconfigureMode = false;
    document.getElementById('custom-modal-title').textContent = '手动配置房间';
    document.getElementById('custom-modal-submit').textContent = '创建';
  }
}

function populateCustomModal(data) {
  // This is a simplified version – a real implementation would receive
  // available options from the server. For now we hardcode common choices.
  const witchSel = document.getElementById('c-witch-rule');
  if (!witchSel.options.length) {
    [
      ['仅第一夜可自救', '仅第一夜可自救'],
      ['始终可自救',     '始终可自救'],
      ['不可自救',       '不可自救'],
    ].forEach(([label, val]) => {
      const o = document.createElement('option');
      o.value = val; o.textContent = label;
      witchSel.appendChild(o);
    });
  }
  const guardSel = document.getElementById('c-guard-rule');
  if (!guardSel.options.length) {
    [
      ['同时被守被救时，对象死亡', '同时被守被救时，对象死亡'],
      ['同时被守被救时，对象存活', '同时被守被救时，对象存活'],
    ].forEach(([label, val]) => {
      const o = document.createElement('option');
      o.value = val; o.textContent = label;
      guardSel.appendChild(o);
    });
  }
  const bombSel = document.getElementById('c-bomb-rule');
  if (!bombSel.options.length) {
    [
      ['单爆吞警徽', '单爆吞警徽'],
      ['双爆吞警徽', '双爆吞警徽'],
    ].forEach(([label, val]) => {
      const o = document.createElement('option');
      o.value = val; o.textContent = label;
      bombSel.appendChild(o);
    });
  }

  // God-role checkboxes (hardcoded common roles)
  const godWolfDiv = document.getElementById('c-god-wolf-opts');
  if (!godWolfDiv.children.length) {
    godWolfDiv.innerHTML = '<strong>特殊狼：</strong><br>';
    ['狼王', '白狼王', '梦魇', '狼美人'].forEach(role => {
      godWolfDiv.innerHTML += `<label style="margin-right:12px;"><input type="checkbox" name="god_wolf" value="${role}"> ${role}</label>`;
    });
  }
  const godCitizenDiv = document.getElementById('c-god-citizen-opts');
  if (!godCitizenDiv.children.length) {
    godCitizenDiv.innerHTML = '<strong>特殊村民：</strong><br>';
    ['预言家', '女巫', '守卫', '猎人', '摄梦人', '白痴', '混血儿', '九尾妖狐'].forEach(role => {
      godCitizenDiv.innerHTML += `<label style="margin-right:12px;"><input type="checkbox" name="god_citizen" value="${role}"> ${role}</label>`;
    });
  }
}

function submitCustomRoom() {
  const config = {
    wolf_num:      parseInt(document.getElementById('c-wolf').value) || 0,
    citizen_num:   parseInt(document.getElementById('c-citizen').value) || 0,
    god_wolf:      [...document.querySelectorAll('input[name="god_wolf"]:checked')].map(el => el.value),
    god_citizen:   [...document.querySelectorAll('input[name="god_citizen"]:checked')].map(el => el.value),
    witch_rule:    document.getElementById('c-witch-rule').value,
    guard_rule:    document.getElementById('c-guard-rule').value,
    sheriff_bomb_rule: document.getElementById('c-bomb-rule').value,
  };
  const isReconfigure = _reconfigureMode;
  hideCustomModal();
  if (isReconfigure) {
    socket.emit('configure_room', { config });
  } else {
    socket.emit('create_room', { preset: 'preset_custom', custom: config });
  }
}

// ── Room join ─────────────────────────────────────────────────────────────────
function showJoinModal() {
  refreshLobby();
  document.getElementById('join-modal').style.display = 'block';
  renderJoinRoomList();
}
function hideJoinModal() {
  document.getElementById('join-modal').style.display = 'none';
}

function renderJoinRoomList() {
  const container = document.getElementById('join-room-list');
  if (!lobbyData || !lobbyData.rooms || lobbyData.rooms.length === 0) {
    container.innerHTML = '<p style="color:#888">暂无房间</p>';
    return;
  }
  container.innerHTML = '';
  lobbyData.rooms.forEach(r => {
    const btn = document.createElement('button');
    btn.className = 'btn btn-light';
    btn.style.cssText = 'display:block;width:100%;text-align:left;margin:4px 0;';
    btn.textContent = r.text;
    btn.onclick = () => quickJoin(r.room_id);
    container.appendChild(btn);
  });
}

function quickJoin(roomId) {
  hideJoinModal();
  socket.emit('join_room', { room_id: roomId });
}

function doJoinRoom() {
  const id = document.getElementById('join-room-id').value.trim();
  if (!id) return;
  hideJoinModal();
  socket.emit('join_room', { room_id: id });
}

document.getElementById('join-room-id').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') doJoinRoom();
});

// ── Utilities ─────────────────────────────────────────────────────────────────
function showScreen(id) {
  document.querySelectorAll('.screen').forEach(el => el.classList.remove('active'));
  const target = document.getElementById(id);
  if (target) target.classList.add('active');
}

function setStatus(msg) {
  ['status-bar', 'status-bar2'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = msg;
  });
}

let toastTimer = null;
function showToast(msg, duration) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.style.display = 'block';
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.style.display = 'none'; }, duration || 2500);
}

// ── Auto-reconnect: if we had a token, reconnection is handled in 'connect' handler.
// If the page was refreshed while already in a room, the server will push state and
// we'll navigate to the room screen automatically via the 'state' event.

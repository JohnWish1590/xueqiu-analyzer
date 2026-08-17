/* ============================================================
   雪球大V观点印证分析 · 前端骨架 (app.js)
   纯原生 JS + ECharts。所有数据来自 ./data/*.json 占位文件，
   后续由 Python 后端在 /api/... 提供同结构 JSON 替换。
   注意：本文件不含任何真实抓取 / 分析逻辑。
   ============================================================ */
'use strict';

// 演示用“今天”基准（占位数据以此计算时间窗口）。真实环境由后端返回时间。
const REFERENCE_DATE = new Date('2026-08-13T23:59:59');

const STATE = {
  winDays: 3,    // 默认显示 3 天，与时间线控件「1 天/3 天/自定义」一致
  timelineTab: 'pending',
  currentPerson: null,
  refDate: null,   // 由后端返回的真实“今天”基准
  charts: {},   // ECharts 实例缓存
};

const DATA = {
  pending: [], verified: [], persons: [], predictions: [], evidence: [],
  settings: null, monitor: null,
};

// 用户头像配色：按用户名动态生成（哈希着色），不再内置任何演示/测试人物
const USER_PALETTE = [
  'linear-gradient(135deg,#7aa2ff,#2b5fd9)',
  'linear-gradient(135deg,#ff9a6b,#e23c39)',
  'linear-gradient(135deg,#9b7bff,#5b3fd9)',
  'linear-gradient(135deg,#46c9a3,#16a36a)',
  'linear-gradient(135deg,#ffd166,#f3722c)',
  'linear-gradient(135deg,#4cc9f0,#4361ee)',
  'linear-gradient(135deg,#f15bb5,#9b5de5)',
  'linear-gradient(135deg,#00bbf9,#00f5d4)',
];
function avatarStyle(name){
  const s = String(name || '?');
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return USER_PALETTE[h % USER_PALETTE.length];
}
function avatarChar(name){ return (name || '?').trim().charAt(0); }

// 涨跌语义：A股 涨=红、跌=绿
function dirClass(v){ return (v != null && !isNaN(v) && v >= 0) ? 'up' : 'down'; }
function fmtPct(v){ return (v != null && !isNaN(v)) ? ((v >= 0 ? '+' : '') + v + '%') : '--'; }

// ---------- 工具：时间窗口 ----------
function parseDate(s){ return new Date(String(s).replace(' ', 'T')); }
function fmtTimeSmart(s){
  if (!s || s === '--') return '--';
  const d = parseDate(s);
  if (isNaN(d.getTime())) return s;
  const now = STATE.refDate || new Date();
  const isToday = d.getFullYear() === now.getFullYear() &&
                  d.getMonth() === now.getMonth() &&
                  d.getDate() === now.getDate();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mi = String(d.getMinutes()).padStart(2, '0');
  const ss = String(d.getSeconds()).padStart(2, '0');
  return isToday ? (hh + ':' + mi + ':' + ss) : (mm + '-' + dd + ' ' + hh + ':' + mi + ':' + ss);
}
function daysAgo(created_at){
  const base = STATE.refDate || REFERENCE_DATE;
  const d = parseDate(created_at);
  return Math.floor((base - d) / 86400000);
}

// ---------- 数据加载 ----------
async function fetchJSON(path){
  const r = await fetch(path, { cache: 'no-store' });
  if (!r.ok) throw new Error('加载失败: ' + path + ' (' + r.status + ')');
  return r.json();
}

async function bootstrap(){
  try {
    [DATA.pending, DATA.verified, DATA.persons, DATA.predictions, DATA.evidence, DATA.settings, DATA.monitor] =
      await Promise.all([
        fetchJSON('/api/timeline_pending'),
        fetchJSON('/api/timeline_verified'),
        fetchJSON('/api/persons'),
        fetchJSON('/api/predictions'),
        fetchJSON('/api/evidence_ledger'),
        fetchJSON('/api/settings'),
        fetchJSON('/api/monitor'),
      ]);
    // refDate：优先用后端参考日期；但若它早于真实今天(行情表断更导致)，
    // 则改用真实今天，避免把 8/15-8/16 的帖子算成"未来/负天数"。
    const backendRef = parseDate(DATA.monitor.reference_date || '2026-08-13');
    const realNow = new Date();
    STATE.refDate = (backendRef && backendRef > realNow) ? backendRef : realNow;
  } catch (e) {
    document.querySelector('main').innerHTML =
      '<div class="empty">数据加载失败：' + e.message +
      '<br>请通过静态服务器访问（如 <code>python -m http.server</code>），勿用 file:// 直接打开。</div>';
    return;
  }
  // 先无条件绑定保存按钮：不依赖任何渲染函数，避免视图渲染抛错导致按钮「点了没反应」
  const sb0 = document.getElementById('btnSaveCookie');
  if (sb0 && !sb0._bound){ sb0._bound = true; sb0.onclick = saveManualCookie; }
  const safeRender = (name, fn) => {
    try { fn(); }
    catch (e) { console.error('渲染[' + name + ']失败：', e); }
  };
  // 各视图独立渲染：某个视图抛错（如图表库缺失）不应连坐导致保存按钮等未绑定
  safeRender('状态条', renderStatusbar);
  safeRender('时间线', renderTimeline);
  safeRender('人物', renderPersons);
  safeRender('证据账本', renderEvidence);
  safeRender('预测', renderPredictions);
  safeRender('设置', renderSettings);   // 保存按钮绑定在此，必须执行
  safeRender('监控', renderMonitor);
  startMonitorPoll();
}

/* ===================== 顶部状态条 ===================== */
function renderStatusbar(){
  const m = DATA.monitor;
  const api = document.getElementById('sbApi');
  const apiText = document.getElementById('sbApiText');
  if (m.api_status === 'ok') { api.className = 'pill ok'; apiText.textContent = 'API 正常'; }
  else if (m.api_status === 'warn') { api.className = 'pill wait'; apiText.textContent = 'API 波动'; }
  else { api.className = 'pill bad'; apiText.textContent = 'API 异常'; }

  const ck = document.getElementById('sbCookie');
  if (m.cookie_status === 'valid') { ck.className = 'pill ok'; ck.textContent = 'Cookie 有效'; }
  else { ck.className = 'pill bad'; ck.textContent = 'Cookie 失效'; }

  document.getElementById('sbLast').textContent = fmtTimeSmart(m.last_fetch);
  const sbNext = document.getElementById('sbNext');
  if (m.worker_running) {
    sbNext.textContent = fmtTimeSmart(m.next_poll);
    sbNext.classList.remove('stopped');
  } else {
    sbNext.textContent = '已停止';
    sbNext.classList.add('stopped');
  }
  document.getElementById('sbTotal').textContent = m.fetched_total;

  // 顶部一键启停自动轮询
  const toggle = document.getElementById('sbWorkerToggle');
  if (toggle){
    if (m.worker_running){
      toggle.className = 'pill wait';
      toggle.textContent = '■ 停止自动轮询';
      toggle.title = '点击停止后台自动轮询';
    } else {
      toggle.className = 'pill primary';
      toggle.textContent = '▶ 开启自动轮询';
      toggle.title = '点击开启后台自动轮询';
    }
  }

  // 待验证/已验证计数随窗口动态更新
  const counts = computeCounts();
  document.getElementById('sbPending').textContent = '待验证 ' + counts.pending;
  document.getElementById('sbVerified').textContent = '已验证 ' + counts.verified;
  document.getElementById('navPending').textContent = counts.pending;
  document.getElementById('cntPending').textContent = counts.pending;
  document.getElementById('cntVerified').textContent = counts.verified;
}

function computeCounts(){
  // 「N天内」= 严格小于 N 天（如 1天内=仅今天，3天内=今天+前2天），避免把恰好 N 天前的发言算进来
  const pend = DATA.pending.filter(p => daysAgo(p.created_at) < STATE.winDays);
  const reclass = DATA.pending.filter(p => daysAgo(p.created_at) >= STATE.winDays).length;
  return {
    pending: pend.length,
    verified: DATA.verified.length + reclass,
  };
}

/* ===================== 1. 时间线 ===================== */
function renderTimeline(){
  // 窗口按钮绑定
  document.querySelectorAll('.win[data-w]').forEach(el => {
    el.onclick = () => setWin(parseInt(el.dataset.w, 10));
  });
  document.getElementById('winCustom').onclick = toggleCustom;
  const ci = document.getElementById('customInput');
  ci.onchange = applyCustom;
  ci.onkeydown = e => { if (e.key === 'Enter') applyCustom(); };

  document.getElementById('tabPending').onclick = () => switchTab('pending');
  document.getElementById('tabVerified').onclick = () => switchTab('verified');

  paintTimeline();
}

function paintTimeline(){
  const win = STATE.winDays;
  // 「N天内」= 严格小于 N 天
  const pend = DATA.pending.filter(p => daysAgo(p.created_at) < win);
  const reclass = DATA.pending.filter(p => daysAgo(p.created_at) >= win);
  const verified = DATA.verified.concat(reclass);

  // 计数同步
  const counts = { pending: pend.length, verified: verified.length };
  document.getElementById('sbPending').textContent = '待验证 ' + counts.pending;
  document.getElementById('sbVerified').textContent = '已验证 ' + counts.verified;
  document.getElementById('navPending').textContent = counts.pending;
  document.getElementById('cntPending').textContent = counts.pending;
  document.getElementById('cntVerified').textContent = counts.verified;

  document.getElementById('pendingList').innerHTML =
    pend.length ? groupByDay(pend, pendingCard) : '<div class="empty">当前窗口内无待验证发言</div>';
  document.getElementById('verifiedList').innerHTML =
    verified.length ? groupByDay(verified, verifiedCard) : '<div class="empty">暂无已验证发言</div>';
}

function groupByDay(list, cardFn){
  const groups = {};
  list.forEach(p => {
    const day = (p.created_at || '').split(' ')[0] || '未知日期';
    (groups[day] = groups[day] || []).push(p);
  });
  return Object.keys(groups).sort().reverse().map(day => {
    const meta = 'age ' + daysAgo(day + ' 00:00') + ' 天';
    const cards = groups[day].map(p => {
      try { return cardFn(p); }
      catch (e) { console.error('卡片渲染失败，已跳过：', e, p); return '<div class="pcard" style="color:var(--up)">渲染异常：数据字段缺失</div>'; }
    }).join('');
    return '<div class="day-head"><span class="d">' + day + '</span><span class="meta">' + meta + '</span></div>' + cards;
  }).join('');
}

function subjectBlock(p){
  const s = p.subject || {};
  const stanceCls = s.stance === '看多' ? 'up' : s.stance === '看空' ? 'down' : '';
  let html = '<div class="subject-block"><span class="lab">主体识别</span>' +
    '<span class="chip subject ' + stanceCls + '">' + esc(s.name || '--') + ' ' + (s.code || '') + ' · ' + (s.stance || '--') + ' · ' + (s.horizon || '--') + '</span>';
  if (p.contrast && p.contrast.length) {
    html += p.contrast.map(c =>
      '<span class="chip contrast" title="' + esc(c.note || '仅展示不进回测') + '">' + esc(c.name || '--') + ' 对比</span>'
    ).join(' ');
  }
  return html + '</div>';
}

function interpretBlock(interp){
  if (!interp || !interp.paraphrase) return '';
  const h = interp.horizon || {};
  const confTxt = (h.confidence != null && !isNaN(h.confidence)) ? ('置信 ' + Math.round(h.confidence * 100) + '%') : '';
  let html = '<div class="interpret-block">' +
    '<div class="i-head"><span class="i-lab">AI 解读</span>' +
      '<span class="chip">' + esc(interp.basis || '无法判断') + '</span>' +
      '<span class="chip">' + esc(h.value || '') + (confTxt ? ' · ' + confTxt : '') + '</span></div>' +
    '<div class="i-para">' + esc(interp.paraphrase) + '</div>';
  if (interp.sectors && interp.sectors.length) {
    html += '<div class="i-row"><span class="i-k">板块</span>' +
      interp.sectors.map(s => '<span class="chip subject">' + esc(s) + '</span>').join('') + '</div>';
  }
  if (interp.stocks && interp.stocks.length) {
    html += '<div class="i-row"><span class="i-k">个股</span>' +
      interp.stocks.map(s => '<span class="chip">' + esc(s.name || '') + (s.note ? ' · ' + esc(s.note) : '') + '</span>').join('') + '</div>';
  }
  if (interp.risks && interp.risks.length) {
    html += '<div class="i-row"><span class="i-k">风险</span><span class="muted">' +
      interp.risks.map(r => esc(r)).join('；') + '</span></div>';
  }
  return html + '</div>';
}

function attribGrid(a){
  return '<div class="attrib">' +
    '<div class="attr"><div class="k">大盘β</div><div class="v ' + dirClass(a.index_beta) + '">' + fmtPct(a.index_beta) + '</div></div>' +
    '<div class="attr"><div class="k">板块α</div><div class="v ' + dirClass(a.sector_alpha) + '">' + fmtPct(a.sector_alpha) + '</div></div>' +
    '<div class="attr"><div class="k">个股实际</div><div class="v ' + dirClass(a.stock_actual) + '">' + fmtPct(a.stock_actual) + '</div></div>' +
    '<div class="attr"><div class="k">个股超额α</div><div class="v ' + dirClass(a.stock_alpha) + '">' + fmtPct(a.stock_alpha) + '</div></div>' +
    '</div>';
}

function pendingCard(p){
  const s = p.subject;
  const stanceCls = s.stance === '看多' ? 'up' : s.stance === '看空' ? 'down' : '';
  return '<div class="pcard">' +
    '<div class="top"><div class="avatar" style="background:' + avatarStyle(p.user_name) + '">' + avatarChar(p.user_name) + '</div>' +
      '<div class="nm">' + esc(p.user_name) + '</div>' +
      '<span class="chip subject ' + stanceCls + '">' + esc(s.stance) + ' · ' + esc(s.name) + '</span>' +
      '<span class="tm">' + esc(p.created_at) + ' · ' + postTypeLabel(p.post_type) + '</span></div>' +
    '<div class="text">' + esc(p.text) + '</div>' +
    subjectBlock(p) +
    interpretBlock(p.interpretation) +
    (p.attrib
      ? attribGrid(p.attrib)
      : '<div class="verify muted">β 剥离待验证（窗口未闭合，T+5 后自动计算）</div>') +
    '<div class="verify">该人历史命中率 <div class="bar"><i style="width:' + p.hist_hit_rate + '%"></i></div>' +
      '<span class="muted">' + s.name + ' ' + p.hist_hit_rate + '% · N=' + p.hist_n + '</span></div>' +
    '</div>';
}

function verifiedCard(p){
  const a = p.actual || {};
  const att = p.attrib || {};
  const stanceHit = p.stance_hit || '--';
  const hitCls = p.hit ? (stanceHit.indexOf('看多') >= 0 ? 'long' : stanceHit.indexOf('看空') >= 0 ? 'short' : 'mid') : 'mid';
  return '<div class="pcard">' +
    '<div class="top"><div class="avatar" style="background:' + avatarStyle(p.user_name) + '">' + avatarChar(p.user_name) + '</div>' +
      '<div class="nm">' + esc(p.user_name) + '</div>' +
      '<span class="chip subject">' + esc((p.subject && p.subject.stance) || '--') + ' · ' + esc((p.subject && p.subject.name) || '--') + '</span>' +
      '<span class="tm">' + esc(p.created_at) + '</span></div>' +
    '<div class="text">' + esc(p.text || '') + '</div>' +
    subjectBlock(p) +
    interpretBlock(p.interpretation) +
    '<div class="attrib">' +
      '<div class="attr"><div class="k">大盘β</div><div class="v ' + dirClass(att.index_beta) + '">' + fmtPct(att.index_beta) + '</div></div>' +
      '<div class="attr"><div class="k">板块α</div><div class="v ' + dirClass(att.sector_alpha) + '">' + fmtPct(att.sector_alpha) + '</div></div>' +
      '<div class="attr"><div class="k">T+5 实际</div><div class="v ' + dirClass(a.t5) + '">' + fmtPct(a.t5) + '</div></div>' +
      '<div class="attr"><div class="k">个股超额α</div><div class="v ' + dirClass(att.stock_alpha) + '">' + fmtPct(att.stock_alpha) + '</div></div>' +
    '</div>' +
    (p.verify7 ? priceChannel7d(p.verify7) : '') +
    '<div class="verify"><span class="seg ' + hitCls + '">' + esc(stanceHit) + '</span>' +
      '<span class="muted">T+1 ' + fmtPct(a.t1) + ' / T+5 ' + fmtPct(a.t5) + ' / T+7 ' + fmtPct(a.t7) + ' / T+10 ' + fmtPct(a.t10) + ' / T+20 ' + fmtPct(a.t20) + '</span>' +
      (p.hit ? '' : ' <span class="muted">（未命中）</span>') + '</div>' +
    '</div>';
}

// 7日价格通道：区间极值验证（最低→最高带 + 起点基准线 + T+7 收盘标记）+ 三数字 + 双验证徽标
function priceChannel7d(v){
  const peak = v.peak_ret, trough = v.trough_ret, close = v.ret_7d, excess = v.excess_7d;
  if (peak == null || trough == null || close == null) return '';
  const lo = Math.min(trough, 0);
  const hi = Math.max(peak, 0);
  const span = (hi - lo) || 1;
  const pct = x => ((x - lo) / span) * 100;
  const posPeak = pct(peak), posTrough = pct(trough), posClose = pct(close), posZero = pct(0);
  const cls = x => (x != null && !isNaN(x) && x >= 0) ? 'up' : 'down';
  const endTag = v.hit_7d
    ? '<span class="vtag ok">✓ 终点超额命中</span>'
    : '<span class="vtag bad">✗ 终点未命中</span>';
  const procTag = v.proc_hit
    ? '<span class="vtag ok2">✓ 过程触达观点</span>'
    : '<span class="vtag bad2">✗ 过程未达</span>';
  return '<div class="ch7">' +
    '<div class="ch7-head"><span class="ch7-lab">7日价格通道</span>' +
      '<span class="muted mini">终点超额 ' + fmtPct(excess) + '（剥离沪深300 Beta）</span></div>' +
    '<div class="ch7-track">' +
      '<div class="ch7-band" style="left:' + Math.min(posTrough, posPeak) + '%;right:' + (100 - Math.max(posTrough, posPeak)) + '%"></div>' +
      '<div class="ch7-zero" style="left:' + posZero + '%"></div>' +
      '<div class="ch7-dot ' + cls(peak) + '" style="left:' + posPeak + '%" title="区间最高 ' + fmtPct(peak) + '"></div>' +
      '<div class="ch7-dot ' + cls(trough) + '" style="left:' + posTrough + '%" title="区间最低 ' + fmtPct(trough) + '"></div>' +
      '<div class="ch7-close ' + cls(close) + '" style="left:' + posClose + '%" title="T+7 收盘 ' + fmtPct(close) + '"><i></i></div>' +
    '</div>' +
    '<div class="ch7-leg">' +
      '<span class="ch7-item"><i class="sw trough"></i>区间最低 <b class="' + cls(trough) + '">' + fmtPct(trough) + '</b></span>' +
      '<span class="ch7-item"><i class="sw close"></i>收盘 <b class="' + cls(close) + '">' + fmtPct(close) + '</b></span>' +
      '<span class="ch7-item"><i class="sw peak"></i>区间最高 <b class="' + cls(peak) + '">' + fmtPct(peak) + '</b></span>' +
    '</div>' +
    '<div class="ch7-tags">' + endTag + procTag + '</div>' +
  '</div>';
}

function postTypeLabel(t){ return ({ original: '原帖', long: '长文', reply: '回帖' })[t] || t; }

function switchTab(t){
  STATE.timelineTab = t;
  document.getElementById('tabPending').classList.toggle('active', t === 'pending');
  document.getElementById('tabVerified').classList.toggle('active', t === 'verified');
  document.getElementById('pendingList').style.display = t === 'pending' ? 'block' : 'none';
  document.getElementById('verifiedList').style.display = t === 'verified' ? 'block' : 'none';
  document.getElementById('winWrap').style.display = t === 'pending' ? 'flex' : 'none';
}

function setWin(w){
  STATE.winDays = w;
  document.querySelectorAll('.win').forEach(x => x.classList.remove('active'));
  document.querySelector('.win[data-w="' + w + '"]').classList.add('active');
  document.getElementById('customInput').style.display = 'none';
  paintTimeline();
}
function toggleCustom(){
  const inp = document.getElementById('customInput');
  if (inp.style.display === 'none') {
    document.querySelectorAll('.win').forEach(x => x.classList.remove('active'));
    document.getElementById('winCustom').classList.add('active');
    inp.style.display = 'inline-block'; inp.focus();
  }
}
function applyCustom(){
  const v = parseInt(document.getElementById('customInput').value, 10);
  if (v && v > 0) { STATE.winDays = v; paintTimeline(); }
}

/* ===================== 2. 人物分析 ===================== */
function renderPersons(){
  const list = document.getElementById('personList');
  list.innerHTML = DATA.persons.map(p =>
    '<div class="pi" data-id="' + p.user_id + '">' +
      '<div class="avatar" style="background:' + avatarStyle(p.name) + '">' + avatarChar(p.name) + '</div>' +
      '<div><div style="font-weight:700">' + esc(p.name) + '</div><div class="mini">' + esc(p.desc) + '</div></div>' +
    '</div>'
  ).join('');
  list.querySelectorAll('.pi').forEach(el => {
    el.onclick = () => {
      list.querySelectorAll('.pi').forEach(x => x.classList.remove('active'));
      el.classList.add('active');
      STATE.currentPerson = el.dataset.id;
      paintPerson(el.dataset.id);
    };
  });
  // 默认选中第一个
  if (DATA.persons.length) {
    list.querySelector('.pi').classList.add('active');
    STATE.currentPerson = DATA.persons[0].user_id;
    paintPerson(STATE.currentPerson);
  }
}

function paintPerson(uid){
  const p = DATA.persons.find(x => x.user_id === uid);
  if (!p) return;
  const m = p.matrix;
  const heat = v => {
    const bg = v >= 60 ? 'var(--down-soft)' : v >= 50 ? 'var(--warn-soft)' : 'var(--up-soft)';
    const col = v >= 60 ? 'var(--down)' : v >= 50 ? 'var(--warn)' : 'var(--up)';
    return '<span class="heat" style="background:' + bg + ';color:' + col + '">' + v + '%</span>';
  };
  const calibN = (p.calibration || []).reduce((a, c) => a + (c.n || 0), 0);
  const html =
    '<div class="card"><h3>' + esc(p.name) + ' · 命中率矩阵 <span class="chip">样本 N=' + p.n + '</span> <span class="chip">IC ' + (p.ic >= 0 ? '+' : '') + p.ic + '</span></h3>' +
      '<table class="m"><tr><th>观点</th><th>T+1 日</th><th>T+5 日</th><th>T+10 日</th><th>T+20 日</th><th>N</th></tr>' +
      '<tr><td class="l">看多事件</td><td>' + heat(m.bullish.t1) + '</td><td>' + heat(m.bullish.t5) + '</td><td>' + heat(m.bullish.t10) + '</td><td>' + heat(m.bullish.t20) + '</td><td>' + m.bullish.n + '</td></tr>' +
      '<tr><td class="l">看空事件</td><td>' + heat(m.bearish.t1) + '</td><td>' + heat(m.bearish.t5) + '</td><td>' + heat(m.bearish.t10) + '</td><td>' + heat(m.bearish.t20) + '</td><td>' + m.bearish.n + '</td></tr>' +
      '</table>' +
      '<p class="mini">方向命中率 = 发言后 N 日该板块/个股实际涨跌方向与观点一致的比例。样本偏小时置信区间宽。</p>' +
    '</div>' +
    profileCard(p.profile) +
    '<div class="card"><h3>分板块历史胜率 & IC（ECharts）</h3><div id="sectorChart" class="chart"></div></div>' +
    '<div class="card"><h3>' + esc(p.name) + ' · 置信度校准曲线 <span class="chip">已验证样本 N=' + calibN + '</span></h3>' +
      '<p class="mini">横轴=该人预测置信度分箱，纵轴=该置信区间内的实际命中率；贴近对角线 y=x 表示「说几成把握就真有几成准」。样本不足时显示「数据积累中」。</p>' +
      '<div id="personCalib" class="chart"></div></div>' +
    '<div class="card"><h3>历史发言下钻</h3>' +
      p.history.map(h => {
        const seg = h.stance === '看多' ? 'long' : h.stance === '看空' ? 'short' : 'mid';
        const hitTag = h.hit ? 'up' : 'down';
        return '<div class="pcard" style="box-shadow:none">' +
          '<div class="top"><span class="seg ' + seg + '">' + esc(h.stance) + '</span>' +
          '<span>' + esc(h.created_at) + ' ' + esc(h.subject) + '</span>' +
          '<span class="chip ' + hitTag + '">' + (h.hit ? '命中' : '未中') + '</span>' +
          '<span class="tm">' + esc(h.text) + '</span></div></div>';
      }).join('') +
    '</div>';
  document.getElementById('personMain').innerHTML = html;
  drawSectorChart(p);
  drawPersonCalibration(p);
}

function profileCard(pr){
  if (!pr || !pr.evidence_n) {
    return '<div class="card"><h3>画像 · 证据账本统计</h3>' +
      '<p class="mini">证据积累中——归档的发言越多，画像越准。当前 0 条证据。</p></div>';
  }
  const hd = pr.horizon_detail || {};
  const hdHtml = Object.keys(hd).map(k =>
    '<span class="chip">' + esc(k) + ' ' + hd[k].n + '条 / ' + hd[k].hit_rate + '%</span>'
  ).join('') || '<span class="muted">--</span>';
  return '<div class="card"><h3>画像 · 证据账本统计 <span class="chip">证据 ' + pr.evidence_n + ' 条</span></h3>' +
    '<div class="i-row"><span class="i-k">典型兑现窗口</span><b>' + esc(pr.dominant_horizon || '--') + '</b></div>' +
    '<div class="i-row"><span class="i-k">基准倾向</span><span class="chip">' + esc(pr.relative_or_absolute || '--') + '</span></div>' +
    '<div class="i-row"><span class="i-k">看多命中</span><span>' + (pr.bull_hit_rate != null ? pr.bull_hit_rate + '%' : '--') + '</span></div>' +
    '<div class="i-row"><span class="i-k">看空命中</span><span>' + (pr.bear_hit_rate != null ? pr.bear_hit_rate + '%' : '--') + '</span></div>' +
    '<div class="i-row"><span class="i-k">各尺度</span><span>' + hdHtml + '</span></div>' +
    '<p class="mini">由证据账本自动统计，不靠印象。AI 只给证据，方向对错由你人工打标签沉淀。</p></div>';
}

function drawSectorChart(p){
  const el = document.getElementById('sectorChart');
  if (!el || typeof echarts === 'undefined') return;
  const chart = STATE.charts.sector || (STATE.charts.sector = echarts.init(el));
  const names = p.sectors.map(s => s.sector);
  const hits = p.sectors.map(s => s.hit);
  const ics = p.sectors.map(s => s.ic);
  chart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['历史胜率%', 'IC'], bottom: 0 },
    grid: { left: 40, right: 50, top: 20, bottom: 40 },
    xAxis: { type: 'category', data: names, axisLabel: { interval: 0 } },
    yAxis: [
      { type: 'value', name: '胜率%', min: 0, max: 100 },
      { type: 'value', name: 'IC', min: -0.2, max: 0.2 },
    ],
    series: [
      { name: '历史胜率%', type: 'bar', data: hits, itemStyle: { color: '#2b5fd9' },
        label: { show: true, position: 'top', formatter: '{c}%' } },
      { name: 'IC', type: 'line', yAxisIndex: 1, data: ics, smooth: true,
        lineStyle: { color: '#e23c39' }, itemStyle: { color: '#e23c39' },
        label: { show: true, formatter: p => (p.value >= 0 ? '+' : '') + p.value } },
    ],
  });
  chart.resize();
}

/* ===================== 证据账本 ===================== */
function renderEvidence(){
  const list = document.getElementById('evidenceList');
  const btnArchive = document.getElementById('btnArchiveEvidence');
  const btnBackfill = document.getElementById('btnBackfillInterpretation');
  if (btnArchive && !btnArchive._bound){ btnArchive._bound = true; btnArchive.onclick = archiveEvidence; }
  if (btnBackfill && !btnBackfill._bound){ btnBackfill._bound = true; btnBackfill.onclick = backfillInterpretation; }
  if (!DATA.evidence.length) {
    list.innerHTML = '<div class="empty">暂无归档证据。点「归档已验证发言」生成，或先「补解读存量发言」。</div>';
    return;
  }
  list.innerHTML = DATA.evidence.map(evidenceCard).join('');
  list.querySelectorAll('[data-tag]').forEach(el => {
    el.onclick = () => tagEvidence(el.dataset.pid, el.dataset.tag);
  });
}

function evidenceCard(e){
  const interp = e.interpretation || {};
  const hitCls = e.hit ? 'long' : 'short';
  const tag = e.manual_tag || '';
  const tagBtns = ['对', '错', '部分对', '存疑'].map(t =>
    '<button class="btn' + (tag === t ? ' primary' : '') + '" data-pid="' + esc(e.pid) + '" data-tag="' + t + '" style="padding:2px 10px;font-size:12px">' + t + '</button>'
  ).join('');
  return '<div class="pcard">' +
    '<div class="top"><div class="avatar" style="background:' + avatarStyle(e.user_name) + '">' + avatarChar(e.user_name) + '</div>' +
      '<div class="nm">' + esc(e.user_name) + '</div>' +
      '<span class="chip subject">' + esc(e.stance) + '</span>' +
      '<span class="chip">' + esc(e.horizon) + ' · T+' + e.expected_window_days + '</span>' +
      '<span class="tm">' + esc(e.created_at) + '</span></div>' +
    interpretBlock(interp) +
    '<div class="attrib">' +
      '<div class="attr"><div class="k">超额收益</div><div class="v ' + dirClass(e.excess_ret) + '">' + fmtPct(e.excess_ret) + '</div></div>' +
      '<div class="attr"><div class="k">实际收益</div><div class="v ' + dirClass(e.actual_ret) + '">' + fmtPct(e.actual_ret) + '</div></div>' +
      '<div class="attr"><div class="k">最大回撤</div><div class="v down">' + (e.mdd != null ? '-' + e.mdd + '%' : '--') + '</div></div>' +
      '<div class="attr"><div class="k">跌停天数</div><div class="v">' + (e.limit_down_days || 0) + '</div></div>' +
    '</div>' +
    '<div class="verify"><span class="seg ' + hitCls + '">' + (e.hit ? '超额方向命中' : '超额未命中') + '</span>' +
      '<span class="muted">回撤速度 ' + (e.drawdown_speed != null ? e.drawdown_speed + ' 日' : '--') + '（正=冲高回落）</span>' +
      '<span style="margin-left:auto;display:flex;gap:6px">' + tagBtns + '</span></div>' +
    '</div>';
}

function archiveEvidence(){
  const hint = document.getElementById('evidenceHint');
  if (hint){ hint.style.display = 'inline'; hint.className = 'hint'; hint.textContent = '归档中…'; }
  fetch('/api/archive_evidence', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
    .then(r => r.json()).then(d => {
      if (hint){ hint.className = 'hint ok'; hint.textContent = '归档完成：' + (d.archived || 0) + ' 条'; }
      setTimeout(reloadEvidence, 600);
    }).catch(e => { if (hint){ hint.className = 'hint err'; hint.textContent = '归档失败：' + e.message; } });
}

function backfillInterpretation(){
  const hint = document.getElementById('evidenceHint');
  if (hint){ hint.style.display = 'inline'; hint.className = 'hint'; hint.textContent = '补解读已启动（后台进行，完成后刷新可见）'; }
  fetch('/api/backfill_interpretation', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
    .then(r => r.json()).then(d => {
      if (hint){ hint.className = 'hint ok'; hint.textContent = d.message || '已启动'; }
    }).catch(e => { if (hint){ hint.className = 'hint err'; hint.textContent = '失败：' + e.message; } });
}

function tagEvidence(pid, tag){
  fetch('/api/tag_evidence', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pid: pid, tag: tag }),
  }).then(r => r.json()).then(d => { if (d.ok) reloadEvidence(); });
}

function reloadEvidence(){
  fetchJSON('/api/evidence_ledger').then(d => { DATA.evidence = d; renderEvidence(); }).catch(() => {});
}

/* ===================== 3. 预测中心 ===================== */
function renderPredictions(){
  const list = document.getElementById('predictList');
  list.innerHTML = DATA.predictions.map(pr => {
    const sigCls = pr.signal === '跟随' ? 'follow' : pr.signal === '观望' ? 'hold' : 'reverse';
    const stanceCls = pr.pred_stance === '看多' ? 'up' : pr.pred_stance === '看空' ? 'down' : '';
    return '<div class="pred">' +
      '<div class="top">' +
        '<div class="avatar" style="background:' + avatarStyle(pr.user) + '">' + avatarChar(pr.user) + '</div>' +
        '<div class="nm" style="font-weight:700">' + esc(pr.user) + '</div>' +
        '<span class="chip subject ' + stanceCls + '">' + esc(pr.pred_stance) + ' · ' + esc(pr.subject) + '</span>' +
        '<span class="signal ' + sigCls + '">建议：' + esc(pr.signal) + '</span>' +
        '<span class="tm muted" style="margin-left:auto">预测于抓取后自动生成</span>' +
      '</div>' +
      '<div class="attrib">' +
        '<div class="attr"><div class="k">预判走向</div><div class="v ' + stanceCls + '">' + esc(pr.sector) + ' ' + (pr.pred_stance === '看多' ? '偏多' : pr.pred_stance === '看空' ? '偏空' : '中性') + '</div></div>' +
        '<div class="attr"><div class="k">置信度</div><div class="v">' + Math.round(pr.confidence * 100) + '%</div></div>' +
        '<div class="attr"><div class="k">涉及板块</div><div class="v" style="font-size:12px">' + esc(pr.involved_sectors.join(' / ')) + '</div></div>' +
        '<div class="attr"><div class="k">持有周期</div><div class="v" style="font-size:13px">' + esc(pr.subject) + '</div></div>' +
      '</div>' +
      '<div class="verify">挂接历史：' + esc(pr.sector) + ' 命中 <b class="' + (pr.hist_sector_hit >= 55 ? 'up' : 'down') + '">' + pr.hist_sector_hit + '%</b> (整体 ' + pr.hist_hit_rate + '%) · 信号权重' +
        (pr.signal === '跟随' ? '高' : pr.signal === '观望' ? '中' : '低') + '</div>' +
      '</div>';
  }).join('');
  drawCalibration();
}

function _calibPointsFor(uid){
  // 返回校准点数组 [[conf%, actual%, n], ...]；若有选中用户则只取该人，否则聚合全部人
  if (uid){
    const p = DATA.persons.find(x => x.user_id === uid);
    if (p && p.calibration) return p.calibration.map(c => [Math.round(c.conf*100), Math.round(c.actual*100), c.n]);
    return [];
  }
  const pts = [];
  DATA.persons.forEach(p => (p.calibration || []).forEach(c =>
    pts.push([Math.round(c.conf*100), Math.round(c.actual*100), c.n])));
  return pts;
}

function _renderCalibChart(chart, el, pts){
  if (!pts.length){
    if (chart){ try { chart.dispose(); } catch(e){} }
    el.innerHTML = '<div class="empty" style="padding:24px;text-align:center;color:var(--muted)">该用户暂无足够已验证预测，校准曲线数据积累中…（抓取并验证更多发言后自动出现）</div>';
    return null;
  }
  el.innerHTML = '';
  if (!chart) chart = echarts.init(el);
  chart.setOption({
    tooltip: { trigger: 'item', formatter: p => '预测 ' + p.value[0] + '% → 实际 ' + p.value[1] + '%（N=' + (p.value[2]||0) + '）' },
    grid: { left: 50, right: 30, top: 30, bottom: 50 },
    xAxis: { type: 'value', name: '预测置信度%', min: 40, max: 100, nameLocation: 'middle', nameGap: 28 },
    yAxis: { type: 'value', name: '实际命中率%', min: 0, max: 100 },
    series: [
      { type: 'line', data: [[40,40],[100,100]], lineStyle: { type: 'dashed', color: '#999' }, symbol: 'none', name: '理想 y=x' },
      { type: 'scatter', data: pts, symbolSize: 14, itemStyle: { color: '#2b5fd9' }, name: '校准点' },
    ],
  });
  chart.resize();
  return chart;
}

function drawCalibration(){
  const el = document.getElementById('calibChart');
  if (!el || typeof echarts === 'undefined') return;
  const uid = STATE.currentPerson;
  const tag = document.getElementById('calibUserTag');
  if (tag){
    const p = uid ? DATA.persons.find(x => x.user_id === uid) : null;
    tag.textContent = p ? ('当前：' + p.name) : '全部（未选人）';
  }
  const pts = _calibPointsFor(uid);
  STATE.charts.calib = _renderCalibChart(STATE.charts.calib, el, pts);
}

function drawPersonCalibration(p){
  const el = document.getElementById('personCalib');
  if (!el || typeof echarts === 'undefined') return;
  const pts = (p.calibration || []).map(c => [Math.round(c.conf*100), Math.round(c.actual*100), c.n]);
  STATE.charts.personCalib = _renderCalibChart(STATE.charts.personCalib, el, pts);
}

/* ===================== 4. 设置 ===================== */
function renderSettings(){
  const s = DATA.settings;
  // 模型选择：下拉由「探测可用模型」动态填充；若已有保存配置，先显示当前配置
  const modelSel = document.getElementById('modelSelect');
  const savedProv = s.model.provider || '';
  const savedModel = s.model.model || '';
  if (savedProv && savedModel){
    let label = savedProv;
    if (s.model_providers && s.model_providers.length){
      const found = s.model_providers.find(p => p.value === savedProv);
      if (found) label = found.label;
    }
    modelSel.innerHTML = '<option value="' + esc(savedProv + '|' + savedModel) + '">' + esc(label + ' · ' + savedModel) + '</option>';
  } else {
    modelSel.innerHTML = '<option value="">请先探测可用模型</option>';
  }
  // 关键修复：不再把掩码 Key 回填进可编辑输入框（否则原样保存会污染真 Key）
  document.getElementById('apiKeyInput').value = '';
  const ks = document.getElementById('apiKeyStatus');
  if (ks) ks.textContent = s.model.api_key_set ? (s.model.api_key + '（已配置）') : '未配置';

  // 发言类型开关
  bindToggle('pt_original', s.post_types.original);
  bindToggle('pt_long', s.post_types.long);
  bindToggle('pt_reply', s.post_types.reply);

  // 默认回填范围（设置页 1/3/30/自定义，保存到 settings.backfill_days）
  bindRangeWrap('bfWrap', 'bfCustom', 'bfCustomInput', s.backfill_days, v => {
    fetch('/api/save_backfill_days', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({days: v})
    }).then(r => r.json()).then(d => {
      if (!d.ok) { alert(d.error || '保存失败'); return; }
      // 同步「立即抓取」旁的快捷范围，避免两个控件显示不一致
      bindRangeWrap('rangeWrap', 'rangeCustom', 'rangeCustomInput', v, null);
    }).catch(e => alert('保存失败: ' + e.message));
  });
  // 立即抓取旁的快捷范围：初始同步默认回填范围
  bindRangeWrap('rangeWrap', 'rangeCustom', 'rangeCustomInput', s.backfill_days, null);

  // 自定义 Skill 开关
  bindToggle('skillToggle', s.skill_enabled);

  // 跟踪大V列表
  const ul = document.getElementById('userList');
  ul.innerHTML = s.followed_users.map(u => {
    const lastAt = u.last_post_at ? ('已抓至 ' + u.last_post_at.slice(0, 10)) : '未抓取';
    const newTag = u.last_new ? '<span class="mini up">＋' + u.last_new + '</span>' : '';
    return '<div class="userline">' +
      '<div class="avatar" style="background:' + avatarStyle(u.name) + '">' + avatarChar(u.name) + '</div>' +
      '<div class="grow"><div style="font-weight:700">' + esc(u.name) + '</div>' +
        '<div class="mini">id ' + esc(u.id) + '</div>' +
        '<div class="fetchstat mini">' + lastAt + ' ' + newTag + '</div></div>' +
      '<div class="check ' + (u.enabled ? 'on' : '') + '" data-id="' + esc(u.id) + '">' + (u.enabled ? '✓' : '') + '</div>' +
    '</div>';
  }).join('');
  // 勾选即实时保存（解决「勾选新人后抓取不生效」）
  ul.querySelectorAll('.check').forEach(c => {
    c.onclick = () => {
      c.classList.toggle('on');
      c.textContent = c.classList.contains('on') ? '✓' : '';
      persistFollowedEnabled();
    };
  });

  // ⑥ 自动轮询服务状态 + 数据持久化信息
  const wp = document.getElementById('workerPill');
  if (wp){
    if (s.worker_running){
      wp.className = 'pill ok'; wp.textContent = '运行中';
    } else {
      wp.className = 'pill'; wp.textContent = '未运行';
    }
  }
  const dbP = document.getElementById('dbPath');
  if (dbP) dbP.textContent = s.db_path || '--';
  const dbN = document.getElementById('dbPosts');
  if (dbN) dbN.textContent = (s.posts_total != null ? s.posts_total : '--');
  const dbL = document.getElementById('dbLastFetch');
  if (dbL) dbL.textContent = (DATA.monitor && DATA.monitor.last_fetch) || '--';

  // Cookie 区（来自 monitor）
  const m = DATA.monitor;
  document.getElementById('setCookieStatus').textContent = m.cookie_status === 'valid' ? '有效' : '失效';
  document.getElementById('setCookieStatus').className = 'pill ' + (m.cookie_status === 'valid' ? 'ok' : 'bad');
  document.getElementById('setCookieExpire').textContent = m.cookie_expire + '（剩 ' + daysBetween(m.cookie_expire, '2026-08-13') + ' 天）';

  // Cookie 来源（动态显示，去掉写死的"雪哨加密库"）
  const srcMap = {login_window:'内置登录窗', manual:'手动粘贴', v2_store:'雪哨加密库(可选)', visitor:'游客(应急)'};
  const srcEl = document.getElementById('setCookieSource');
  if (srcEl) srcEl.textContent = srcMap[DATA.settings.cookie_source] || DATA.settings.cookie_source || '--';

  // 绑定「保存 Cookie」（手动粘贴）
  const sb = document.getElementById('btnSaveCookie');
  if (sb && !sb._bound){ sb._bound = true; sb.onclick = saveManualCookie; }

  // 绑定「AI 模型与密钥」保存 / 探测 / 清除
  const sm = document.getElementById('btnSaveModel');
  if (sm && !sm._bound){ sm._bound = true; sm.onclick = saveModel; }
  const dm = document.getElementById('btnDetectModel');
  if (dm && !dm._bound){ dm._bound = true; dm.onclick = detectModels; }
  const cm = document.getElementById('btnClearModel');
  if (cm && !cm._bound){ cm._bound = true; cm.onclick = clearModel; }
}

function saveModel(){
  const raw = document.getElementById('modelSelect').value;
  const key = document.getElementById('apiKeyInput').value.trim();
  const hint = document.getElementById('modelHint');
  if (!raw){ hint.style.display='inline'; hint.className='hint err'; hint.textContent='请先探测并选择模型'; return; }
  const pipe = raw.indexOf('|');
  const provider = pipe > -1 ? raw.slice(0, pipe) : raw;
  const model = pipe > -1 ? raw.slice(pipe + 1) : '';
  hint.style.display = 'none';
  fetch('/api/save_model', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({provider: provider, model: model, api_key: key}),
  }).then(r => r.json()).then(d => {
    if (!d.ok){ hint.style.display='inline'; hint.className='hint err'; hint.textContent = '保存失败：' + (d.error || ''); return; }
    hint.style.display='inline'; hint.className='hint ok';
    hint.textContent = '已保存：' + provider + (d.model ? ' · ' + d.model : '') + (d.api_key_set ? '（Key 已更新）' : '（保留原 Key）');
    const ks = document.getElementById('apiKeyStatus');
    if (ks) ks.textContent = d.api_key_set ? (d.api_key_masked + '（已配置）') : '未配置';
    document.getElementById('apiKeyInput').value = '';
    // 同步内存态，避免再次渲染时回退
    if (DATA.settings && DATA.settings.model){
      DATA.settings.model.provider = provider;
      DATA.settings.model.model = d.model || '';
      DATA.settings.model.api_key_set = d.api_key_set;
      if (d.api_key_set) DATA.settings.model.api_key = d.api_key_masked;
    }
  }).catch(e=>{ hint.style.display='inline'; hint.className='hint err'; hint.textContent='保存失败：' + e.message; });
}

function detectModels(){
  const key = document.getElementById('apiKeyInput').value.trim();
  const hint = document.getElementById('modelHint');
  if (!key){ hint.style.display='inline'; hint.className='hint err'; hint.textContent='请先粘贴 Key 再探测'; return; }
  hint.style.display = 'inline'; hint.className = 'hint'; hint.textContent = '探测中…';
  fetch('/api/detect_models', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({api_key: key}),
  }).then(r => r.json()).then(d => {
    if (!d.ok){ hint.className = 'hint err'; hint.textContent = '探测失败：' + (d.error || ''); return; }
    const models = d.models || [];
    if (models.length === 0){
      hint.className = 'hint err';
      hint.textContent = '未探测到可用模型（可能 Key 无效或网络受限），请检查 Key 后重试';
      document.getElementById('modelSelect').innerHTML = '<option value="">未探测到模型</option>';
      return;
    }
    const sel = document.getElementById('modelSelect');
    sel.innerHTML = models.map(m => '<option value="' + esc(m.provider + '|' + m.model) + '">' + esc(m.label) + '</option>').join('');
    sel.value = models[0].provider + '|' + models[0].model;
    hint.className = 'hint ok';
    hint.textContent = '探测到 ' + models.length + ' 个可用模型，已自动选第一个';
  }).catch(e=>{ hint.style.display='inline'; hint.className='hint err'; hint.textContent='探测失败：' + e.message; });
}

function clearModel(){
  if (!confirm('确定要清除已保存的 API Key 吗？清除后将降级为本地启发式分析。')) return;
  const hint = document.getElementById('modelHint');
  fetch('/api/clear_model', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({}),
  }).then(r => r.json()).then(d => {
    if (!d.ok){ hint.style.display='inline'; hint.className='hint err'; hint.textContent='清除失败：' + (d.error || ''); return; }
    hint.style.display='inline'; hint.className='hint ok';
    hint.textContent = 'Key 已清除，provider 保持为 ' + d.provider;
    document.getElementById('modelSelect').innerHTML = '<option value="">请先探测可用模型</option>';
    const ks = document.getElementById('apiKeyStatus');
    if (ks) ks.textContent = '未配置';
    document.getElementById('apiKeyInput').value = '';
    if (DATA.settings && DATA.settings.model){
      DATA.settings.model.api_key_set = false;
      DATA.settings.model.api_key = '';
      DATA.settings.model.model = '';
    }
  }).catch(e=>{ hint.style.display='inline'; hint.className='hint err'; hint.textContent='清除失败：' + e.message; });
}

function saveManualCookie(){
  const ta = document.getElementById('manualCookie');
  const hint = document.getElementById('cookieHint');
  const v = (ta.value || '').trim();
  if(!v){
    if (hint){ hint.style.display='block'; hint.className='hint err'; hint.textContent='请先粘贴 Cookie 再保存'; }
    showToast('请先粘贴 Cookie', 'err');
    return;
  }
  fetch('/api/save_cookie', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({cookie:v})})
    .then(r => r.text().then(txt => {
      let d; try { d = JSON.parse(txt); } catch(e){ throw new Error('服务返回异常：' + txt.slice(0,200)); }
      if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
      return d;
    }))
    .then(d=>{
      // 关键修复：HTTP 200 不代表 cookie 真的落盘生效，必须回查 monitor 确认 cookie_status
      return fetchJSON('/api/monitor').then(m=>{
        DATA.monitor = m || DATA.monitor;
        if (m && m.cookie_status === 'valid'){
          if (hint){ hint.style.display='block'; hint.className='hint ok'; hint.textContent='✅ 保存成功，Cookie 已生效'; }
          showToast('Cookie 保存成功并已生效', 'ok');
        } else {
          if (hint){ hint.style.display='block'; hint.className='hint err'; hint.textContent='⚠️ 服务返回成功但未真正生效，请硬刷新(Ctrl+F5)后重试'; }
          showToast('保存未生效，请重试', 'err');
        }
        return fetchJSON('/api/settings').then(s=>{ DATA.settings=s; renderSettings(); renderStatusbar(); });
      });
    })
    .catch(e=>{
      if (hint){ hint.style.display='block'; hint.className='hint err'; hint.textContent='❌ 保存失败：' + e.message; }
      showToast('保存失败: ' + e.message, 'err');
    });
}

function bindToggle(id, on){
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.toggle('on', !!on);
  el.onclick = () => el.classList.toggle('on');
}

function daysBetween(a, b){
  return Math.max(0, Math.floor((parseDate(a) - parseDate(b)) / 86400000));
}

/* ===================== 5. 抓取监控 ===================== */
function renderMonitor(){
  const m = DATA.monitor;
  const cards = [
    { t: 'API 状态', big: apiPill(m.api_status) },
    { t: 'Cookie 有效期', big: '<span class="big up">' + esc(m.cookie_expire) + '</span>' },
    { t: 'WAF 绕过', big: '<span class="big" style="font-size:15px">' + esc(m.waf) + '</span>' },
    { t: '上次抓取', big: '<span class="big">' + esc(m.last_fetch) + '</span>' },
    { t: '下次轮询', big: '<span class="big">' + esc(m.next_poll) + '</span>' },
    { t: '本次新增', big: '<span class="big up">待验证 ' + m.pending + '</span>' },
    { t: '已抓总量', big: '<span class="big">' + m.fetched_total + '</span>' },
    { t: '回填进度', big: '<span class="big">' + m.backfill_progress + '%</span>' },
  ];
  document.getElementById('monGrid').innerHTML = cards.map(c =>
    '<div class="mon"><div class="t">' + c.t + '</div><div class="big">' + c.big + '</div></div>'
  ).join('');

  const log = document.getElementById('monLog');
  log.innerHTML = m.logs.map(l =>
    '<span class="line ' + (l.level === 'ok' ? 'ok' : l.level === 'err' ? 'err' : 'w') + '">[' + esc(l.time) + '] ' + esc(l.msg) + '</span>'
  ).join('');

  // 回填进度条
  const prog = document.createElement('div');
  prog.className = 'card';
  prog.style.marginTop = '14px';
  prog.innerHTML = '<h3>全量回填进度</h3><div class="bar" style="height:10px;max-width:none"><i style="width:' + m.backfill_progress + '%"></i></div>' +
    '<p class="mini" style="margin-top:8px">进度 ' + m.backfill_progress + '% · 已抓 ' + m.fetched_total + ' 条 · 待验证 ' + m.pending + ' · 已验证 ' + m.verified + '</p>';
  log.parentNode.appendChild(prog);
}

function apiPill(st){
  if (st === 'ok') return '<span class="pill ok"><span class="live"></span>正常</span>';
  if (st === 'warn') return '<span class="pill wait">波动</span>';
  return '<span class="pill bad">异常</span>';
}

/* ===================== 视图切换 ===================== */
document.querySelectorAll('nav.side a').forEach(a => {
  a.onclick = () => {
    document.querySelectorAll('nav.side a').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    a.classList.add('active');
    const v = document.getElementById(a.dataset.view);
    v.classList.add('active');
    // 显示隐藏图表需要 resize
    if (a.dataset.view === 'person' && STATE.charts.sector) STATE.charts.sector.resize();
    if (a.dataset.view === 'predict' && STATE.charts.calib) STATE.charts.calib.resize();
  };
});

/* ===================== 工具：转义 ===================== */
function esc(s){
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

window.addEventListener('resize', () => {
  Object.values(STATE.charts).forEach(c => c && c.resize && c.resize());
});

// ---------- 读取「特别关注」分组并勾选保存 ----------
function loadSpecialFollowed(){
  const hint = document.getElementById('groupLoadHint');
  hint.textContent = '正在读取…';
  fetch('/api/followed_groups', {method:'POST'}).then(r=>r.json()).then(d=>{
    if(!d.ok){ hint.textContent = d.error || '读取失败'; return; }
    openGroupModal(d.users);
    hint.textContent = '已读取 ' + d.users.length + ' 人，勾选后点「保存选中」。';
  }).catch(e=>{ hint.textContent = '读取失败: ' + e.message; });
}

function openGroupModal(users){
  // 弹窗默认勾选：当前已启用的人员保留选中，新增人员默认不选中
  const existing = new Set(
    (DATA.settings && DATA.settings.followed_users || [])
      .filter(u => u.enabled)
      .map(u => String(u.id))
  );
  const list = document.getElementById('groupList');
  list.innerHTML = users.map(u => {
    const checked = existing.has(String(u.id)) ? 'checked' : '';
    return '<label class="gitem"><input type="checkbox" ' + checked + ' data-id="' + esc(u.id) + '" data-name="' + esc(u.name) + '"> ' +
           '<span class="gname">' + esc(u.name) + '</span> <span class="gid">' + esc(u.id) + '</span></label>';
  }).join('');
  document.getElementById('groupCount').textContent = '共 ' + users.length + ' 人（请勾选要跟踪的人）';
  document.getElementById('groupModal').style.display = 'flex';
  // 顶部「全选」按实际勾选情况刷新
  const allChecked = users.length > 0 && users.every(u => existing.has(String(u.id)));
  document.getElementById('groupCheckAll').checked = allChecked;
}

function saveFollowedPayload(list){
  return fetch('/api/save_followed', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(list)})
    .then(r=>r.json()).then(d=>{
      if(!d.ok){ alert(d.error || '保存失败'); throw new Error(d.error); }
      return fetchJSON('/api/settings').then(s=>{ DATA.settings = s; renderSettings(); });
    });
}

function saveGroupSelection(){
  // 合并保存：保留弹窗外人员的原有启用状态；弹窗内人员按当前勾选状态更新
  const checkedIds = new Set(
    [...document.querySelectorAll('#groupList input[type=checkbox]:checked')]
      .map(c => String(c.dataset.id))
  );
  const nameById = new Map(
    [...document.querySelectorAll('#groupList input[type=checkbox]')]
      .map(c => [String(c.dataset.id), c.dataset.name])
  );

  const mergedMap = new Map();
  (DATA.settings && DATA.settings.followed_users || []).forEach(u => {
    mergedMap.set(String(u.id), {id: String(u.id), name: u.name, enabled: !!u.enabled});
  });

  nameById.forEach((name, id) => {
    mergedMap.set(id, {id: id, name: name, enabled: checkedIds.has(id)});
  });

  const payload = Array.from(mergedMap.values());
  fetch('/api/save_followed', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)})
    .then(r=>r.json()).then(d=>{
      if(!d.ok){ alert(d.error || '保存失败'); throw new Error(d.error); }
      return fetchJSON('/api/settings').then(s=>{ DATA.settings = s; renderSettings(); });
    }).then(() => {
      document.getElementById('groupModal').style.display = 'none';
    }).catch(()=>{});
}

// 设置页勾选变化：把当前所有人（含勾选态）整体保存，确保新增勾选即时生效
function persistFollowedEnabled(){
  const state = {};
  document.querySelectorAll('#userList .check[data-id]').forEach(c => {
    state[c.dataset.id] = c.classList.contains('on');
  });
  const payload = DATA.settings.followed_users.map(u => ({
    id: u.id, name: u.name,
    enabled: state[u.id] !== undefined ? state[u.id] : !!u.enabled,
  }));
  fetch('/api/save_followed', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)})
    .then(r=>r.json()).then(d=>{
      if(!d.ok){ alert(d.error || '保存失败'); return; }
      return fetchJSON('/api/settings').then(s=>{ DATA.settings = s; });
    }).catch(e=>console.error('保存失败', e));
}

// 解析并新增：合并进现有列表（不覆盖其他人）
function saveFollowedMerge(list){
  const map = {};
  DATA.settings.followed_users.forEach(u => { map[u.id] = {id:u.id, name:u.name, enabled:true}; });
  list.forEach(u => { map[u.id] = {id:u.id, name:u.name, enabled:true}; });
  const merged = Object.values(map);
  return fetch('/api/save_followed', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(merged)})
    .then(r=>r.json()).then(d=>{
      if(!d.ok){ alert(d.error || '保存失败'); throw new Error(d.error); }
      return fetchJSON('/api/settings').then(s=>{ DATA.settings = s; renderSettings(); });
    });
}

function resolveUser(){
  const input = document.getElementById('newUserInput');
  const q = input.value.trim();
  if(!q){ alert('请输入昵称或 user_id'); return; }
  fetch('/api/resolve_user', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({q})})
    .then(r=>r.json()).then(d=>{
      if(!d.ok){ alert(d.error || '解析失败'); return; }
      const u = d.user;
      return saveFollowedMerge([{id:u.id, name:u.name}]).then(()=>{ input.value = ''; });
    }).catch(e=>alert('解析失败: ' + e.message));
}

document.getElementById('btnLoadSpecial').onclick = loadSpecialFollowed;
document.getElementById('btnResolve').onclick = resolveUser;
document.getElementById('groupModalClose').onclick = () => document.getElementById('groupModal').style.display = 'none';
document.getElementById('groupCancel').onclick = () => document.getElementById('groupModal').style.display = 'none';
document.getElementById('groupCheckAll').onchange = e => {
  document.querySelectorAll('#groupList input[type=checkbox]').forEach(c => c.checked = e.target.checked);
};
document.getElementById('groupSave').onclick = saveGroupSelection;
document.getElementById('btnStartFetch').onclick = startFetch;
document.getElementById('btnWorkerStart').onclick = startWorker;
document.getElementById('btnWorkerStop').onclick = stopWorker;

document.getElementById('sbWorkerToggle').onclick = function(){
  const m = DATA.monitor;
  if (!m) return;
  if (m.worker_running) stopWorker(); else startWorker();
};

document.getElementById('sbShutdown').onclick = shutdownServer;
function shutdownServer(){
  if (!confirm('确定关闭雪球分析服务吗？\n\n关闭后网页将不可用，需双击 launch_app.bat 重新启动。\n\n（仅退出本程序，不影响剪思盒等其他 Python 进程）')) return;
  // 先显示遮罩，避免等待可能失败的网络请求
  showShutdownScreen();
  fetch('/api/shutdown', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'})
    .catch(()=>{})
    .finally(()=>{ /* server 即将退出，遮罩已显示 */ });
}
function showShutdownScreen(){
  if (document.getElementById('shutdownScreen')) return;
  const div = document.createElement('div');
  div.id = 'shutdownScreen';
  div.style.cssText = 'position:fixed;inset:0;background:#f4f6fa;display:flex;flex-direction:column;align-items:center;justify-content:center;z-index:999;font-family:sans-serif;color:#1f2329;text-align:center;padding:24px';
  div.innerHTML = '<div style="font-size:44px;margin-bottom:12px">⏻</div>'
    + '<div style="font-size:20px;font-weight:700;margin-bottom:8px">服务已关闭</div>'
    + '<div style="font-size:14px;color:#646a73;max-width:440px;line-height:1.8">本程序已退出，端口 8765 已释放。<br>如需重新使用，请双击 <b>launch_app.bat</b> 重新启动（不会与已关闭的实例冲突）。</div>';
  document.body.appendChild(div);
}

// 时间范围选择（1天/3天/30天/自定义），影响“立即抓取”的 days 参数
bindRangeWrap('rangeWrap', 'rangeCustom', 'rangeCustomInput', null, null);
function bindRangeWrap(wrapId, customBtnId, customInputId, initialDays, onChange){
  const wrap = document.getElementById(wrapId);
  if (!wrap) return;
  const customBtn = document.getElementById(customBtnId);
  const customInput = document.getElementById(customInputId);
  if (!customBtn || !customInput) return;
  // 初始化状态：匹配 initialDays
  if (initialDays) {
    let matched = false;
    wrap.querySelectorAll('.rbtn[data-d]').forEach(b => {
      if (parseInt(b.dataset.d, 10) === initialDays) {
        b.classList.add('active');
        matched = true;
      } else {
        b.classList.remove('active');
      }
    });
    if (!matched) {
      customBtn.classList.add('active');
      customInput.value = String(initialDays);
      customInput.style.display = '';
    } else {
      customBtn.classList.remove('active');
      customInput.style.display = 'none';
    }
  }
  wrap.querySelectorAll('.rbtn[data-d]').forEach(b => {
    b.onclick = () => {
      wrap.querySelectorAll('.rbtn').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      customInput.style.display = 'none';
      const d = parseInt(b.dataset.d, 10);
      if (onChange) onChange(d);
    };
  });
  customBtn.onclick = () => {
    wrap.querySelectorAll('.rbtn').forEach(x => x.classList.remove('active'));
    customBtn.classList.add('active');
    customInput.style.display = '';
    customInput.focus();
  };
  customInput.onchange = () => {
    const v = parseInt(customInput.value, 10);
    if (v > 0 && v <= 365 && onChange) onChange(v);
  };
  customInput.onkeydown = e => { if (e.key === 'Enter') customInput.blur(); };
}
function getSelectedDays(){
  return getWrapDays('rangeWrap', 'rangeCustom', 'rangeCustomInput');
}
function getWrapDays(wrapId, customBtnId, customInputId){
  const wrap = document.getElementById(wrapId);
  if (!wrap) return null;
  const active = wrap.querySelector('.rbtn.active');
  if (!active) return null;
  const customBtn = document.getElementById(customBtnId);
  if (active === customBtn){
    const v = parseInt(document.getElementById(customInputId).value, 10);
    return (v > 0 && v <= 365) ? v : null;
  }
  const d = parseInt(active.dataset.d, 10);
  return (d > 0) ? d : null;
}

// ---------- 抓取完成/错误提示去重（monitor 对象每轮 poll 会被替换，不能把状态存在上面） ----------
let _lastFetchStage = null;
let _lastFetchCount = null;

// ---------- 自动轮询 worker 控制 ----------
function refreshWorker(){
  return fetchJSON('/api/worker/status').then(w => {
    const wp = document.getElementById('workerPill');
    if (wp){
      if (w.running){ wp.className = 'pill ok'; wp.textContent = '运行中'; }
      else { wp.className = 'pill'; wp.textContent = '未运行'; }
    }
    const hint = document.getElementById('workerHint');
    if (hint) hint.textContent = w.running
      ? ('每 ' + w.interval_minutes + ' 分钟自动增量抓取 · 上次 ' + w.last_fetch)
      : '点击「开启」后，后台将按间隔自动抓取已勾选人员';
  }).catch(()=>{});
}
function startWorker(){
  fetch('/api/worker/start', {method:'POST'}).then(r=>r.json()).then(d=>{
    if(!d.ok) return alert(d.error||'启动失败');
    refreshMonitor().catch(()=>{});
  }).catch(e=>alert('启动失败: '+e.message));
}
function stopWorker(){
  fetch('/api/worker/stop', {method:'POST'}).then(r=>r.json()).then(d=>{
    if(!d.ok) return alert(d.error||'停止失败');
    refreshMonitor().catch(()=>{});
  }).catch(e=>alert('停止失败: '+e.message));
}

// ---------- 抓取状态轮询与展示 ----------
let _pollTimer = null;
function refreshMonitor(){
  return fetchJSON('/api/monitor').then(m => {
    DATA.monitor = m;
    renderStatusbar();
    renderMonitor();
    renderFetchStatus();
    refreshWorker();
  });
}
function startMonitorPoll(){
  if (_pollTimer) return;
  refreshMonitor().catch(()=>{});
  _pollTimer = setInterval(() => { refreshMonitor().catch(()=>{}); }, 2000);
}
function startFetch(){
  const btn = document.getElementById('btnStartFetch');
  const days = getSelectedDays();

  // 立即本地反馈：禁用按钮 + 状态条进入抓取中
  if (btn){ btn.textContent = '抓取中…'; btn.disabled = true; }
  DATA.monitor = DATA.monitor || {};
  DATA.monitor.fetch_running = true;
  DATA.monitor.fetch_stage = '抓取中';
  DATA.monitor.fetch_message = '正在抓取选中人员数据…';
  renderFetchStatus();
  renderStatusbar();

  const body = days ? JSON.stringify({days}) : '{}';
  fetch('/api/start_fetch', {method:'POST', headers:{'Content-Type':'application/json'}, body})
    .then(r => r.json()).then(d => {
      if (!d.ok) { showToast(d.error || '启动失败', 'err'); return; }
      const n = (DATA.settings && DATA.settings.followed_users || []).filter(u => u.enabled).length;
      showToast('抓取已启动：正在抓取 ' + n + ' 位选中人员', 'ok');
      return refreshMonitor();
    }).catch(e => {
      showToast('启动失败: ' + e.message, 'err');
    });
}

function showToast(msg, type){
  // 安全：若已存在同内容 toast，先移除，避免重叠造成「一直不消失」的错觉
  document.querySelectorAll('.toast').forEach(t => {
    if (t.textContent === msg) t.remove();
  });
  const id = 'toast_' + Date.now();
  const el = document.createElement('div');
  el.id = id;
  el.className = 'toast ' + (type || '');
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => {
    el.style.opacity = '0';
    setTimeout(() => el.remove(), 300);
  }, 2000);
}
function renderFetchStatus(){
  const m = DATA.monitor;
  if (!m) return;
  const running = m.fetch_running;

  // 「立即抓取」按钮状态与后台同步
  const btn = document.getElementById('btnStartFetch');
  if (btn){
    if (running){
      btn.textContent = '抓取中…';
      btn.disabled = true;
    } else {
      btn.textContent = '立即抓取选中人员';
      btn.disabled = false;
    }
  }

  const pill = document.getElementById('sbFetch');
  if (pill){
    if (running){
      pill.className = 'pill wait';
      pill.innerHTML = '<span class="spin"></span>' + esc(m.fetch_message || '正在抓取选中人员数据…');
    } else if (m.fetch_stage === '完成'){
      pill.className = 'pill ok';
      const count = (typeof m.fetch_count === 'number') ? m.fetch_count : 0;
      pill.textContent = '抓取完成 · 新增 ' + count + ' 条';
      // 完成时弹出一次提示，避免用户因抓取太快而误以为没反应；
      // 用模块级变量去重，因为 DATA.monitor 每 2 秒会被 refreshMonitor() 整体替换
      if (_lastFetchStage !== '完成' || _lastFetchCount !== count){
        showToast('抓取完成：新增 ' + count + ' 条待验证', 'ok');
      }
    } else if (m.fetch_stage === '错误'){
      pill.className = 'pill bad';
      pill.textContent = m.fetch_message || '抓取失败';
      if (_lastFetchStage !== '错误'){
        showToast('抓取失败：' + (m.fetch_message || '未知错误'), 'err');
      }
    } else {
      pill.className = 'pill';
      pill.textContent = '未抓取';
    }
  }
  _lastFetchStage = m.fetch_stage || null;
  _lastFetchCount = (typeof m.fetch_count === 'number') ? m.fetch_count : 0;
  // 时间线横幅
  const banner = document.getElementById('timelineBanner');
  if (banner){
    if (running){
      banner.style.display = 'block';
      banner.textContent = (m.fetch_message || '正在抓取选中人员数据…') +
        (m.fetch_current_user ? '（当前：' + m.fetch_current_user + '）' : '');
    } else {
      banner.style.display = 'none';
    }
  }
  // 设置页跟踪列表逐人状态：只有真正在本次抓取范围内（enabled）的人才显示“抓取中…”
  const followedMap = new Map();
  if (DATA.settings && DATA.settings.followed_users) {
    DATA.settings.followed_users.forEach(u => followedMap.set(String(u.id), u.enabled));
  }
  document.querySelectorAll('#userList .userline').forEach(el => {
    const tag = el.querySelector('.fetchstat');
    const check = el.querySelector('.check[data-id]');
    if (!tag) return;
    const uid = check ? check.dataset.id : null;
    const enabled = uid ? followedMap.get(uid) : false;
    if (running && enabled){
      const isCurrent = uid && String(DATA.monitor.fetch_current_user || '') === String(DATA.settings.followed_users.find(u => String(u.id) === uid)?.name || '');
      tag.textContent = isCurrent ? '正在抓取…' : '抓取中…';
      tag.className = 'mini fetchstat fetching';
    } else if (uid && !enabled) {
      tag.textContent = '未启用';
      tag.className = 'mini fetchstat idle';
    } else {
      // 未抓取时显示「已抓至 X」或「未抓取」（来自 fetch_log）
      const fu = DATA.settings && DATA.settings.followed_users
        ? DATA.settings.followed_users.find(u => String(u.id) === uid) : null;
      tag.textContent = (fu && fu.last_post_at) ? ('已抓至 ' + fu.last_post_at.slice(0,10)) : '未抓取';
      tag.className = 'mini fetchstat';
    }
  });
  // 监控页进度卡
  const prog = document.getElementById('fetchProg');
  if (prog){
    if (running && m.fetch_total){
      const pct = Math.round((m.fetch_index || 0) / m.fetch_total * 100);
      prog.style.display = 'block';
      prog.innerHTML = '<h3>实时抓取进度</h3><div class="bar" style="height:10px"><i style="width:' + pct + '%"></i></div>' +
        '<p class="mini">第 ' + (m.fetch_index||0) + ' / ' + m.fetch_total + ' 人 · 已抓 ' + (m.fetch_count||0) + ' 条' +
        (m.fetch_current_user ? ' · 当前：' + esc(m.fetch_current_user) : '') + '</p>';
    } else {
      prog.style.display = 'none';
    }
  }
}

bootstrap();

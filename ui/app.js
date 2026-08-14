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
  pending: [], verified: [], persons: [], predictions: [],
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
function dirClass(v){ return v >= 0 ? 'up' : 'down'; }
function fmtPct(v){ return (v >= 0 ? '+' : '') + v + '%'; }

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
    [DATA.pending, DATA.verified, DATA.persons, DATA.predictions, DATA.settings, DATA.monitor] =
      await Promise.all([
        fetchJSON('/api/timeline_pending'),
        fetchJSON('/api/timeline_verified'),
        fetchJSON('/api/persons'),
        fetchJSON('/api/predictions'),
        fetchJSON('/api/settings'),
        fetchJSON('/api/monitor'),
      ]);
    STATE.refDate = parseDate(DATA.monitor.reference_date || '2026-08-13');
  } catch (e) {
    document.querySelector('main').innerHTML =
      '<div class="empty">数据加载失败：' + e.message +
      '<br>请通过静态服务器访问（如 <code>python -m http.server</code>），勿用 file:// 直接打开。</div>';
    return;
  }
  renderStatusbar();
  renderTimeline();
  renderPersons();
  renderPredictions();
  renderSettings();
  renderMonitor();
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
    const day = p.created_at.split(' ')[0];
    (groups[day] = groups[day] || []).push(p);
  });
  return Object.keys(groups).sort().reverse().map(day => {
    const meta = 'age ' + daysAgo(day + ' 00:00') + ' 天';
    const cards = groups[day].map(cardFn).join('');
    return '<div class="day-head"><span class="d">' + day + '</span><span class="meta">' + meta + '</span></div>' + cards;
  }).join('');
}

function subjectBlock(p){
  const s = p.subject;
  const stanceCls = s.stance === '看多' ? 'up' : s.stance === '看空' ? 'down' : '';
  let html = '<div class="subject-block"><span class="lab">主体识别</span>' +
    '<span class="chip subject ' + stanceCls + '">' + esc(s.name) + ' ' + (s.code || '') + ' · ' + s.stance + ' · ' + s.horizon + '</span>';
  if (p.contrast && p.contrast.length) {
    html += p.contrast.map(c =>
      '<span class="chip contrast" title="' + esc(c.note || '仅展示不进回测') + '">' + esc(c.name) + ' 对比</span>'
    ).join(' ');
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
    (p.attrib
      ? attribGrid(p.attrib)
      : '<div class="verify muted">β 剥离待验证（窗口未闭合，T+5 后自动计算）</div>') +
    '<div class="verify">该人历史命中率 <div class="bar"><i style="width:' + p.hist_hit_rate + '%"></i></div>' +
      '<span class="muted">' + s.name + ' ' + p.hist_hit_rate + '% · N=' + p.hist_n + '</span></div>' +
    '</div>';
}

function verifiedCard(p){
  const a = p.actual;
  const hitCls = p.hit ? (p.stance_hit.indexOf('看多') >= 0 ? 'long' : 'short') : 'mid';
  return '<div class="pcard">' +
    '<div class="top"><div class="avatar" style="background:' + avatarStyle(p.user_name) + '">' + avatarChar(p.user_name) + '</div>' +
      '<div class="nm">' + esc(p.user_name) + '</div>' +
      '<span class="chip subject">' + esc(p.subject.stance) + ' · ' + esc(p.subject.name) + '</span>' +
      '<span class="tm">' + esc(p.created_at) + '</span></div>' +
    '<div class="text">' + esc(p.text) + '</div>' +
    subjectBlock(p) +
    '<div class="attrib">' +
      '<div class="attr"><div class="k">大盘β</div><div class="v ' + dirClass(p.attrib.index_beta) + '">' + fmtPct(p.attrib.index_beta) + '</div></div>' +
      '<div class="attr"><div class="k">板块α</div><div class="v ' + dirClass(p.attrib.sector_alpha) + '">' + fmtPct(p.attrib.sector_alpha) + '</div></div>' +
      '<div class="attr"><div class="k">T+5 实际</div><div class="v ' + dirClass(a.t5) + '">' + fmtPct(a.t5) + '</div></div>' +
      '<div class="attr"><div class="k">个股超额α</div><div class="v ' + dirClass(p.attrib.stock_alpha) + '">' + fmtPct(p.attrib.stock_alpha) + '</div></div>' +
    '</div>' +
    '<div class="verify"><span class="seg ' + hitCls + '">' + esc(p.stance_hit) + '</span>' +
      '<span class="muted">T+1 ' + fmtPct(a.t1) + ' / T+5 ' + fmtPct(a.t5) + ' / T+10 ' + fmtPct(a.t10) + ' / T+20 ' + fmtPct(a.t20) + '</span>' +
      (p.hit ? '' : ' <span class="muted">（未命中）</span>') + '</div>' +
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
  const html =
    '<div class="card"><h3>' + esc(p.name) + ' · 命中率矩阵 <span class="chip">样本 N=' + p.n + '</span> <span class="chip">IC ' + (p.ic >= 0 ? '+' : '') + p.ic + '</span></h3>' +
      '<table class="m"><tr><th>观点</th><th>T+1 日</th><th>T+5 日</th><th>T+10 日</th><th>T+20 日</th><th>N</th></tr>' +
      '<tr><td class="l">看多事件</td><td>' + heat(m.bullish.t1) + '</td><td>' + heat(m.bullish.t5) + '</td><td>' + heat(m.bullish.t10) + '</td><td>' + heat(m.bullish.t20) + '</td><td>' + m.bullish.n + '</td></tr>' +
      '<tr><td class="l">看空事件</td><td>' + heat(m.bearish.t1) + '</td><td>' + heat(m.bearish.t5) + '</td><td>' + heat(m.bearish.t10) + '</td><td>' + heat(m.bearish.t20) + '</td><td>' + m.bearish.n + '</td></tr>' +
      '</table>' +
      '<p class="mini">方向命中率 = 发言后 N 日该板块/个股实际涨跌方向与观点一致的比例。样本偏小时置信区间宽。</p>' +
    '</div>' +
    '<div class="card"><h3>分板块历史胜率 & IC（ECharts）</h3><div id="sectorChart" class="chart"></div></div>' +
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
}

function drawSectorChart(p){
  const el = document.getElementById('sectorChart');
  if (!el) return;
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

function drawCalibration(){
  const el = document.getElementById('calibChart');
  if (!el) return;
  const chart = STATE.charts.calib || (STATE.charts.calib = echarts.init(el));
  // 聚合所有预测校准点：{conf 置信度(0-1), actual 实际命中率(0-1)}
  const pts = [];
  DATA.predictions.forEach(pr => (pr.calibration || []).forEach(c =>
    pts.push([Math.round(c.conf * 100), Math.round(c.actual * 100)])));
  chart.setOption({
    tooltip: { trigger: 'item', formatter: p => '预测 ' + p.value[0] + '% → 实际 ' + p.value[1] + '%' },
    grid: { left: 50, right: 30, top: 30, bottom: 50 },
    xAxis: { type: 'value', name: '预测置信度%', min: 40, max: 90, nameLocation: 'middle', nameGap: 28 },
    yAxis: { type: 'value', name: '实际命中率%', min: 40, max: 90 },
    series: [
      // 参考对角线 y=x
      { type: 'line', data: [[40,40],[90,90]], lineStyle: { type: 'dashed', color: '#999' }, symbol: 'none', name: '理想 y=x' },
      { type: 'scatter', data: pts, symbolSize: 12, itemStyle: { color: '#2b5fd9' }, name: '校准点' },
    ],
  });
  chart.resize();
}

/* ===================== 4. 设置 ===================== */
function renderSettings(){
  const s = DATA.settings;
  // 模型选择
  const modelSel = document.getElementById('modelSelect');
  const PROVIDERS = [
    { v: 'deepseek-flash', t: 'DeepSeek V4 Flash（默认·高速低价）' },
    { v: 'deepseek-pro', t: 'DeepSeek V4 Pro（高精度）' },
    { v: 'qwen', t: '通义千问 · qwen-plus' },
    { v: 'glm', t: '智谱 GLM · glm-4-flash' },
  ];
  modelSel.innerHTML = PROVIDERS.map(p => '<option value="' + p.v + '">' + p.t + '</option>').join('');
  modelSel.value = s.model.provider;
  document.getElementById('apiKeyInput').value = s.model.api_key;

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

  // 绑定「从浏览器导入」与「手动粘贴保存」
  const ib = document.getElementById('btnImportCookie');
  if (ib && !ib._bound){ ib._bound = true; ib.onclick = importBrowserCookie; }
  const sb = document.getElementById('btnSaveCookie');
  if (sb && !sb._bound){ sb._bound = true; sb.onclick = saveManualCookie; }
}

function importBrowserCookie(){
  const btn = document.getElementById('btnImportCookie');
  const hint = document.getElementById('importHint');
  const sel = document.getElementById('browserSel');
  const browser = sel ? sel.value : 'chrome';
  btn.disabled = true;
  hint.style.display = 'block';
  hint.textContent = '正在从 ' + browser + ' 读取已登录的雪球 Cookie…（若浏览器正运行，请先关闭后再试）';
  fetch('/api/import_browser_cookie', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({browser})})
    .then(r=>r.json())
    .then(d=>{
      btn.disabled = false;
      if(!d.ok){
        hint.style.display = 'block';
        hint.textContent = '❌ ' + (d.error || '导入失败');
        showToast(d.error || '导入失败', 'err');
        return;
      }
      hint.textContent = '✅ ' + (d.message || '导入成功');
      setTimeout(()=>{ hint.style.display='none'; }, 5000);
      showToast('已从浏览器导入 Cookie', 'ok');
      return fetchJSON('/api/settings').then(s=>{ DATA.settings = s; renderSettings(); });
    })
    .catch(e=>{
      btn.disabled = false;
      hint.style.display = 'block';
      hint.textContent = '❌ 请求失败：' + e.message;
      showToast('失败: '+e.message, 'err');
    });
}

function saveManualCookie(){
  const ta = document.getElementById('manualCookie');
  const v = (ta.value || '').trim();
  if(!v){ showToast('请先粘贴 Cookie', 'err'); return; }
  fetch('/api/save_cookie', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({cookie:v})})
    .then(r=>r.json()).then(d=>{
      if(!d.ok){ showToast(d.error||'保存失败', 'err'); return; }
      showToast('Cookie 已保存', 'ok');
      ta.value='';
      return fetchJSON('/api/settings').then(s=>{ DATA.settings=s; renderSettings(); });
    }).catch(e=>showToast('保存失败: '+e.message, 'err'));
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
  const id = 'toast_' + Date.now();
  const el = document.createElement('div');
  el.id = id;
  el.className = 'toast ' + (type || '');
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => {
    el.style.opacity = '0';
    setTimeout(() => el.remove(), 300);
  }, 3000);
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
      pill.textContent = '抓取完成 · 新增 ' + (m.fetch_count || 0) + ' 条';
      // 完成时弹出一次提示，避免用户因抓取太快而误以为没反应
      if (!m._toast_shown){
        m._toast_shown = true;
        showToast('抓取完成：新增 ' + (m.fetch_count || 0) + ' 条待验证', 'ok');
      }
    } else if (m.fetch_stage === '错误'){
      pill.className = 'pill bad';
      pill.textContent = m.fetch_message || '抓取失败';
      if (!m._toast_shown){
        m._toast_shown = true;
        showToast('抓取失败：' + (m.fetch_message || '未知错误'), 'err');
      }
    } else {
      pill.className = 'pill';
      pill.textContent = '未抓取';
      m._toast_shown = false;
    }
  }
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

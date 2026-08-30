/* 투자 대시보드 — app logic
 * Reads data/latest.json (authoritative latest per index) and
 * data/index_closes.csv (full history) and renders KPI cards, sparklines,
 * day-range bars, an indexed comparison line chart, and a records table.
 * No build step, no dependencies. Charts are hand-drawn SVG.
 */
'use strict';

/* -------------------------------------------------------------------------- */
/* Config                                                                     */
/* -------------------------------------------------------------------------- */
const INDEX_META = {
  '.IXIC': { name: '나스닥 종합', short: '나스닥',  cssVar: '--s-ixic', order: 0 },
  '.SPX':  { name: 'S&P 500',    short: 'S&P 500', cssVar: '--s-spx',  order: 1 },
  '.DJI':  { name: '다우존스',    short: '다우',    cssVar: '--s-dji',  order: 2 },
};
const SYMBOL_ORDER = Object.keys(INDEX_META).sort((a, b) => INDEX_META[a].order - INDEX_META[b].order);

const STATUS_MAP = {
  REG_MKT:   { label: '정규장',      open: true  },
  PRE_MKT:   { label: '장 시작 전',  open: false },
  POST_MKT:  { label: '장 마감 후',  open: false },
  AFTER_MKT: { label: '장 마감 후',  open: false },
  CLOSED:    { label: '휴장',        open: false },
};

const state = { latest: null, history: [], rangeDays: 0, usingFallback: false };

/* -------------------------------------------------------------------------- */
/* Tiny helpers                                                               */
/* -------------------------------------------------------------------------- */
const $ = (sel, root = document) => root.querySelector(sel);
const SVGNS = 'http://www.w3.org/2000/svg';

function svg(tag, attrs = {}, kids = []) {
  const n = document.createElementNS(SVGNS, tag);
  for (const k in attrs) if (attrs[k] != null) n.setAttribute(k, attrs[k]);
  for (const c of [].concat(kids)) if (c) n.appendChild(c);
  return n;
}
function el(tag, attrs = {}, kids = []) {
  const n = document.createElement(tag);
  for (const k in attrs) {
    if (k === 'class') n.className = attrs[k];
    else if (k === 'html') n.innerHTML = attrs[k];
    else if (k === 'text') n.textContent = attrs[k];
    else if (k.startsWith('--')) n.style.setProperty(k, attrs[k]);
    else if (attrs[k] != null) n.setAttribute(k, attrs[k]);
  }
  for (const c of [].concat(kids)) if (c != null) n.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  return n;
}
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/* number / date formatting */
const nf = (dp) => new Intl.NumberFormat('ko-KR', { minimumFractionDigits: dp, maximumFractionDigits: dp });
const fmt = (x, dp = 2) => (Number.isFinite(x) ? nf(dp).format(x) : '—');
const MINUS = '−';
function signed(x, dp = 2) {
  if (!Number.isFinite(x)) return '—';
  const s = x > 0 ? '+' : x < 0 ? MINUS : '';
  return s + fmt(Math.abs(x), dp);
}
const pct = (x, dp = 2) => (Number.isFinite(x) ? signed(x, dp) + '%' : '—');
const dirOf = (x) => (x > 0 ? 'up' : x < 0 ? 'down' : 'flat');
const arrowOf = (x) => (x > 0 ? '▲' : x < 0 ? '▼' : '·');

function partsKST(d) {
  const p = new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).formatToParts(d).reduce((o, x) => ((o[x.type] = x.value), o), {});
  return p;
}
function fmtDateTimeKST(d) {
  const p = partsKST(d);
  return `${p.year}.${p.month}.${p.day} ${p.hour}:${p.minute}`;
}
function fmtDateMD(d) {
  const p = partsKST(d);
  return `${Number(p.month)}/${Number(p.day)}`;
}

/* CSV parse (handles quoted fields; our data has none, but be safe) */
function parseCSV(text) {
  const rows = [];
  const lines = text.replace(/\r\n/g, '\n').split('\n').filter((l) => l.trim().length);
  if (!lines.length) return rows;
  const header = splitCSVLine(lines[0]);
  const nums = new Set(['close', 'previous_close', 'change', 'change_pct', 'open', 'day_high', 'day_low']);
  for (let i = 1; i < lines.length; i++) {
    const cells = splitCSVLine(lines[i]);
    const obj = {};
    header.forEach((h, j) => {
      const v = cells[j];
      obj[h] = nums.has(h) ? Number(v) : v;
    });
    rows.push(obj);
  }
  return rows;
}
function splitCSVLine(line) {
  const out = []; let cur = ''; let q = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (q) {
      if (c === '"' && line[i + 1] === '"') { cur += '"'; i++; }
      else if (c === '"') q = false;
      else cur += c;
    } else if (c === '"') q = true;
    else if (c === ',') { out.push(cur); cur = ''; }
    else cur += c;
  }
  out.push(cur);
  return out.map((s) => s.trim());
}

/* -------------------------------------------------------------------------- */
/* Data loading                                                               */
/* -------------------------------------------------------------------------- */
async function loadData() {
  const bust = `?t=${Date.now()}`;
  let latest = null, history = null, fellBack = false;

  try {
    const [lj, hc] = await Promise.all([
      fetch('./data/latest.json' + bust, { cache: 'no-store' }).then((r) => { if (!r.ok) throw 0; return r.json(); }),
      fetch('./data/index_closes.csv' + bust, { cache: 'no-store' }).then((r) => { if (!r.ok) throw 0; return r.text(); }),
    ]);
    latest = lj;
    history = parseCSV(hc);
  } catch (e) {
    fellBack = true;
    latest = JSON.parse($('#fallback-latest').textContent);
    history = parseCSV($('#fallback-history').textContent);
  }

  state.latest = latest;
  state.history = (history || []).filter((r) => r.symbol && Number.isFinite(r.close));
  state.usingFallback = fellBack;
}

/* group history rows by symbol, sorted ascending by time */
function seriesBySymbol() {
  const map = {};
  for (const sym of SYMBOL_ORDER) map[sym] = [];
  for (const r of state.history) {
    if (!map[r.symbol]) map[r.symbol] = [];
    map[r.symbol].push({ t: new Date(r.captured_kst).getTime(), close: r.close, row: r });
  }
  for (const sym in map) map[sym].sort((a, b) => a.t - b.t);
  return map;
}
function windowStart() {
  if (!state.rangeDays) return -Infinity;
  return Date.now() - state.rangeDays * 86400000;
}

/* -------------------------------------------------------------------------- */
/* Render: status strip                                                       */
/* -------------------------------------------------------------------------- */
function renderStatus() {
  const idx = state.latest?.indices || [];
  const first = idx[0];
  const badge = $('#market-badge');
  const note = $('#market-note');
  const asof = $('#asof');

  if (!first) { badge.textContent = '데이터 없음'; return; }
  const st = STATUS_MAP[first.market_status] || { label: first.market_status || '알 수 없음', open: false };
  badge.textContent = st.label;
  badge.className = 'badge ' + (st.open ? 'badge--open' : 'badge--closed');

  const d = new Date(state.latest.captured_kst);
  asof.textContent = fmtDateTimeKST(d) + ' KST';
  note.textContent = `미국 동부시각 ${first.market_time_et || '—'} 기준 · ${idx.length}개 지수`;
}

/* -------------------------------------------------------------------------- */
/* Render: KPI cards                                                          */
/* -------------------------------------------------------------------------- */
function renderKPIs() {
  const grid = $('#kpi-grid');
  grid.innerHTML = '';
  const series = seriesBySymbol();
  const bySym = {};
  for (const it of state.latest?.indices || []) bySym[it.symbol] = it;

  for (const sym of SYMBOL_ORDER) {
    const it = bySym[sym];
    const meta = INDEX_META[sym];
    if (!it || !meta) continue;

    const dir = dirOf(it.change);
    const card = el('article', { class: 'kpi-card', '--accent': `var(${meta.cssVar})` });

    // head
    card.appendChild(el('div', { class: 'kpi-card__head' }, [
      el('span', { class: 'kpi-card__name' }, [el('span', { class: 'kpi-card__dot' }), meta.name]),
      el('span', { class: 'kpi-card__sym' }, sym),
    ]));

    // value
    card.appendChild(el('div', { class: 'kpi-card__value' }, fmt(it.close, 2)));

    // delta
    card.appendChild(el('div', { class: 'kpi-card__delta' }, [
      el('span', { class: `delta-pill delta-pill--${dir}` }, `${arrowOf(it.change)} ${signed(it.change, 2)}`),
      el('span', { class: 'delta-sub' }, `${pct(it.change_pct)} · 전일 ${fmt(it.previous_close, 2)}`),
    ]));

    // sparkline
    const spark = el('div', { class: 'kpi-card__spark' });
    card.appendChild(spark);

    // day-range bar
    const range = el('div', { class: 'kpi-range' });
    const bar = el('div', { class: 'kpi-range__bar' });
    range.appendChild(bar);
    range.appendChild(el('div', { class: 'kpi-range__labels' }, [
      el('span', {}, `저가 ${fmt(it.day_low, 2)}`),
      el('span', {}, `고가 ${fmt(it.day_high, 2)}`),
    ]));
    card.appendChild(range);

    // mini stats
    card.appendChild(el('dl', { class: 'kpi-stats' }, [
      statCell('시가', fmt(it.open, 2)),
      statCell('고가', fmt(it.day_high, 2)),
      statCell('저가', fmt(it.day_low, 2)),
      statCell('전일', fmt(it.previous_close, 2)),
    ]));

    grid.appendChild(card);

    // draw SVGs after attach (need width)
    drawSparkline(spark, (series[sym] || []).filter((p) => p.t >= windowStart()), it.change);
    drawRangeBar(bar, it);
  }
}
function statCell(label, val) {
  return el('div', {}, [el('dt', {}, label), el('dd', {}, val)]);
}

/* -------------------------------------------------------------------------- */
/* Render: sparkline                                                          */
/* -------------------------------------------------------------------------- */
function drawSparkline(container, points, todayChange) {
  container.innerHTML = '';
  const W = Math.max(120, container.clientWidth || 240);
  const H = 46, pad = 4;
  const s = svg('svg', { viewBox: `0 0 ${W} ${H}`, width: '100%', height: H, preserveAspectRatio: 'none' });

  // direction color: net over window, fall back to today's change
  const net = points.length >= 2 ? points[points.length - 1].close - points[0].close : todayChange;
  const dir = dirOf(net);
  const color = dir === 'up' ? cssVar('--up') : dir === 'down' ? cssVar('--down') : cssVar('--flat');

  if (points.length < 2) {
    // single point → a centered dot
    s.appendChild(svg('circle', { cx: W / 2, cy: H / 2, r: 4, fill: color }));
    s.appendChild(svg('circle', { cx: W / 2, cy: H / 2, r: 4, fill: 'none', stroke: cssVar('--surface'), 'stroke-width': 2 }));
    container.appendChild(s);
    return;
  }

  const xs = points.map((p) => p.t);
  const ys = points.map((p) => p.close);
  const xMin = Math.min(...xs), xMax = Math.max(...xs);
  const yMin = Math.min(...ys), yMax = Math.max(...ys);
  const X = (t) => pad + (xMax === xMin ? 0.5 : (t - xMin) / (xMax - xMin)) * (W - pad * 2);
  const Y = (v) => pad + (yMax === yMin ? 0.5 : 1 - (v - yMin) / (yMax - yMin)) * (H - pad * 2);

  const dLine = points.map((p, i) => `${i ? 'L' : 'M'}${X(p.t).toFixed(1)},${Y(p.close).toFixed(1)}`).join(' ');
  const dArea = `${dLine} L${X(xMax).toFixed(1)},${H - pad} L${X(xMin).toFixed(1)},${H - pad} Z`;

  s.appendChild(svg('path', { d: dArea, fill: color, 'fill-opacity': 0.1, stroke: 'none' }));
  s.appendChild(svg('path', { d: dLine, fill: 'none', stroke: color, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
  const last = points[points.length - 1];
  s.appendChild(svg('circle', { cx: X(last.t), cy: Y(last.close), r: 3.5, fill: color, stroke: cssVar('--surface'), 'stroke-width': 2 }));
  container.appendChild(s);
}

/* -------------------------------------------------------------------------- */
/* Render: day-range bar                                                      */
/* -------------------------------------------------------------------------- */
function drawRangeBar(container, it) {
  container.innerHTML = '';
  const W = Math.max(140, container.clientWidth || 280);
  const H = 20, r = 6, cy = H / 2;
  const s = svg('svg', { viewBox: `0 0 ${W} ${H}`, width: '100%', height: H });

  const lo = it.day_low, hi = it.day_high;
  const span = hi - lo || 1;
  const x0 = r + 2, x1 = W - r - 2;
  const pos = (v) => x0 + Math.min(1, Math.max(0, (v - lo) / span)) * (x1 - x0);

  // track (low..high)
  s.appendChild(svg('rect', { x: x0, y: cy - 3, width: x1 - x0, height: 6, rx: 3, fill: cssVar('--grid') }));

  // previous close reference tick
  if (Number.isFinite(it.previous_close)) {
    const px = pos(it.previous_close);
    s.appendChild(svg('line', { x1: px, y1: cy - 7, x2: px, y2: cy + 7, stroke: cssVar('--muted'), 'stroke-width': 2, 'stroke-linecap': 'round' }));
  }
  // close marker
  const dir = dirOf(it.change);
  const color = dir === 'up' ? cssVar('--up') : dir === 'down' ? cssVar('--down') : cssVar('--flat');
  const cx = pos(it.close);
  s.appendChild(svg('circle', { cx, cy, r, fill: color, stroke: cssVar('--surface'), 'stroke-width': 2 }));

  const tip = `저가 ${fmt(lo)} · 종가 ${fmt(it.close)} · 고가 ${fmt(hi)} · 전일 ${fmt(it.previous_close)}`;
  s.appendChild(svg('title', {}, [document.createTextNode(tip)]));
  container.appendChild(s);
}

/* -------------------------------------------------------------------------- */
/* Render: comparison line chart (indexed to window start = 0%)              */
/* -------------------------------------------------------------------------- */
function renderComparison() {
  const container = $('#cmp-chart');
  const empty = $('#cmp-empty');
  container.innerHTML = '';
  container.style.position = 'relative';

  const raw = seriesBySymbol();
  const start = windowStart();

  // build indexed series within window
  const seriesList = [];
  for (const sym of SYMBOL_ORDER) {
    const pts = (raw[sym] || []).filter((p) => p.t >= start);
    if (!pts.length) continue;
    const base = pts[0].close;
    seriesList.push({
      sym, meta: INDEX_META[sym], color: cssVar(INDEX_META[sym].cssVar),
      pts: pts.map((p) => ({ t: p.t, v: (p.close / base - 1) * 100, close: p.close })),
    });
  }

  const totalPts = seriesList.reduce((n, s) => n + s.pts.length, 0);
  const uniqTimes = [...new Set(seriesList.flatMap((s) => s.pts.map((p) => p.t)))].sort((a, b) => a - b);

  if (!seriesList.length || uniqTimes.length === 0) {
    empty.hidden = false;
    empty.textContent = '표시할 데이터가 아직 없습니다.';
    return;
  }
  if (uniqTimes.length < 2) {
    empty.hidden = false;
    empty.textContent = '추이는 종가가 2일치 이상 쌓이면 선 그래프로 표시됩니다. (매 거래일 자동 누적)';
  } else {
    empty.hidden = true;
  }

  const W = Math.max(280, container.clientWidth || 640);
  const H = Math.round(Math.max(240, Math.min(360, W * 0.42)));
  const m = { top: 18, right: 62, bottom: 30, left: 48 };
  const x0 = m.left, x1 = W - m.right, y0 = m.top, y1 = H - m.bottom;

  const tMin = uniqTimes[0], tMax = uniqTimes[uniqTimes.length - 1];
  let vMin = Math.min(0, ...seriesList.flatMap((s) => s.pts.map((p) => p.v)));
  let vMax = Math.max(0, ...seriesList.flatMap((s) => s.pts.map((p) => p.v)));
  if (vMin === vMax) { vMin -= 1; vMax += 1; }
  const padV = (vMax - vMin) * 0.12; vMin -= padV; vMax += padV;

  const X = (t) => (tMax === tMin ? (x0 + x1) / 2 : x0 + (t - tMin) / (tMax - tMin) * (x1 - x0));
  const Y = (v) => y1 - (v - vMin) / (vMax - vMin) * (y1 - y0);

  const s = svg('svg', { viewBox: `0 0 ${W} ${H}`, width: '100%', height: 'auto', role: 'img', 'aria-label': '지수별 상대 수익률 비교 선그래프' });

  // y gridlines + labels (nice ticks)
  const ticks = niceTicks(vMin, vMax, 5);
  for (const tk of ticks) {
    const yy = Y(tk);
    const isZero = Math.abs(tk) < 1e-9;
    s.appendChild(svg('line', { x1: x0, y1: yy, x2: x1, y2: yy, class: isZero ? 'zero-line' : 'gridline' }));
    s.appendChild(svg('text', { x: x0 - 8, y: yy + 3.5, 'text-anchor': 'end', class: 'ax-label' }, [tt(`${tk > 0 ? '+' : ''}${fmt(tk, tk % 1 ? 1 : 0)}%`)]));
  }

  // x axis ticks
  const xticks = pickTimeTicks(uniqTimes, 6);
  for (const t of xticks) {
    s.appendChild(svg('text', { x: X(t), y: y1 + 18, 'text-anchor': 'middle', class: 'ax-label' }, [tt(fmtDateMD(new Date(t)))]));
  }

  // series
  const endLabels = [];
  for (const ser of seriesList) {
    if (ser.pts.length >= 2) {
      const d = ser.pts.map((p, i) => `${i ? 'L' : 'M'}${X(p.t).toFixed(1)},${Y(p.v).toFixed(1)}`).join(' ');
      s.appendChild(svg('path', { d, fill: 'none', stroke: ser.color, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
    }
    const last = ser.pts[ser.pts.length - 1];
    s.appendChild(svg('circle', { cx: X(last.t), cy: Y(last.v), r: 4, fill: ser.color, stroke: cssVar('--surface'), 'stroke-width': 2 }));
    // value-only end label (series names live in the legend); keeps the label short
    endLabels.push({ y: Y(last.v), color: ser.color, text: `${signed(last.v, 2)}%`, x: X(last.t) });
  }

  // de-collide end labels vertically
  endLabels.sort((a, b) => a.y - b.y);
  const minGap = 15;
  for (let i = 1; i < endLabels.length; i++) {
    if (endLabels[i].y - endLabels[i - 1].y < minGap) endLabels[i].y = endLabels[i - 1].y + minGap;
  }
  for (const lb of endLabels) {
    s.appendChild(svg('circle', { cx: x1 + 9, cy: lb.y - 3.5, r: 3, fill: lb.color }));
    s.appendChild(svg('text', { x: x1 + 15, y: lb.y, class: 'ax-label', style: `fill:${cssVar('--text-2')};font-weight:600` }, [tt(lb.text)]));
  }

  container.appendChild(s);

  // hover layer (only when we have a line)
  if (uniqTimes.length >= 2) attachHover(container, s, { seriesList, uniqTimes, X, Y, x0, x1, y0, y1, tMin, tMax });
}

function tt(str) { return document.createTextNode(str); }

function niceTicks(min, max, count) {
  const span = max - min || 1;
  const step0 = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const norm = step0 / mag;
  const step = (norm >= 5 ? 5 : norm >= 2 ? 2 : 1) * mag;
  const out = [];
  const startv = Math.ceil(min / step) * step;
  for (let v = startv; v <= max + 1e-9; v += step) out.push(Math.round(v * 1e6) / 1e6);
  return out;
}
function pickTimeTicks(times, count) {
  if (times.length <= count) return times;
  const out = [];
  const step = (times.length - 1) / (count - 1);
  for (let i = 0; i < count; i++) out.push(times[Math.round(i * step)]);
  return [...new Set(out)];
}

/* -------------------------------------------------------------------------- */
/* Comparison chart hover                                                     */
/* -------------------------------------------------------------------------- */
function attachHover(container, s, ctx) {
  const { seriesList, uniqTimes, X, Y, y0, y1, x0, x1, tMin, tMax } = ctx;

  const cross = svg('line', { y1: y0, y2: y1, class: 'gridline', 'stroke-width': 1, opacity: 0 });
  s.appendChild(cross);
  const dots = seriesList.map((ser) => {
    const c = svg('circle', { r: 4.5, fill: ser.color, stroke: cssVar('--surface'), 'stroke-width': 2, opacity: 0 });
    s.appendChild(c);
    return c;
  });
  const tip = el('div', { class: 'chart-tip' });
  tip.style.opacity = '0';
  container.appendChild(tip);

  const overlay = svg('rect', { x: x0, y: y0, width: Math.max(1, x1 - x0), height: Math.max(1, y1 - y0), fill: 'transparent', style: 'cursor:crosshair' });
  s.appendChild(overlay);

  function nearestTime(px) {
    const t = tMin + (px - x0) / (x1 - x0) * (tMax - tMin);
    let best = uniqTimes[0], bd = Infinity;
    for (const u of uniqTimes) { const d = Math.abs(u - t); if (d < bd) { bd = d; best = u; } }
    return best;
  }
  function move(ev) {
    const rect = s.getBoundingClientRect();
    // pointer x in SVG user units (viewBox maps to displayed width)
    const px = (ev.clientX - rect.left) * (s.viewBox.baseVal.width / rect.width);
    const t = nearestTime(px);
    const cxp = X(t);
    cross.setAttribute('x1', cxp); cross.setAttribute('x2', cxp); cross.setAttribute('opacity', 1);

    const rows = [];
    seriesList.forEach((ser, i) => {
      const p = ser.pts.find((pp) => pp.t === t);
      if (p) {
        dots[i].setAttribute('cx', cxp); dots[i].setAttribute('cy', Y(p.v)); dots[i].setAttribute('opacity', 1);
        rows.push({ color: ser.color, name: ser.meta.short, v: p.v, close: p.close });
      } else dots[i].setAttribute('opacity', 0);
    });

    tip.innerHTML = '';
    tip.appendChild(el('div', { class: 'chart-tip__date' }, fmtDateMD(new Date(t)) + ' · ' + fmtDateTimeKST(new Date(t))));
    for (const r of rows) {
      tip.appendChild(el('div', { class: 'chart-tip__row' }, [
        el('span', { class: 'chart-tip__key' }, [el('span', { class: 'chart-tip__sw', '--c': r.color }), r.name]),
        el('span', { class: 'chart-tip__val' }, `${signed(r.v, 2)}%  ·  ${fmt(r.close, 2)}`),
      ]));
    }
    // position tip (convert user x → container px)
    const contRect = container.getBoundingClientRect();
    const pxDisp = cxp * (rect.width / s.viewBox.baseVal.width) + (rect.left - contRect.left);
    tip.style.left = Math.max(70, Math.min(container.clientWidth - 70, pxDisp)) + 'px';
    tip.style.top = (Y(Math.max(...rows.map((r) => r.v))) * (rect.height / s.viewBox.baseVal.height)) + 'px';
    tip.style.opacity = '1';
  }
  function leave() {
    cross.setAttribute('opacity', 0);
    dots.forEach((d) => d.setAttribute('opacity', 0));
    tip.style.opacity = '0';
  }
  overlay.addEventListener('pointermove', move);
  overlay.addEventListener('pointerleave', leave);
  overlay.addEventListener('pointerdown', move);
}

/* -------------------------------------------------------------------------- */
/* Render: legend + table                                                     */
/* -------------------------------------------------------------------------- */
function renderLegend() {
  const box = $('#cmp-legend');
  box.innerHTML = '';
  for (const sym of SYMBOL_ORDER) {
    const meta = INDEX_META[sym];
    box.appendChild(el('span', { class: 'legend__item', role: 'listitem' }, [
      el('span', { class: 'legend__swatch', '--c': `var(${meta.cssVar})` }),
      meta.short,
    ]));
  }
}

function renderTable() {
  const body = $('#tbl-body');
  body.innerHTML = '';
  const rows = [...state.history].sort((a, b) => {
    const dt = new Date(b.captured_kst) - new Date(a.captured_kst);
    return dt !== 0 ? dt : (INDEX_META[a.symbol]?.order ?? 9) - (INDEX_META[b.symbol]?.order ?? 9);
  }).slice(0, 45);

  if (!rows.length) {
    body.appendChild(el('tr', {}, [el('td', { colspan: 8, class: 'loading' }, '기록이 없습니다.')]));
    return;
  }
  for (const r of rows) {
    const meta = INDEX_META[r.symbol] || { short: r.index, cssVar: '--flat' };
    const dir = dirOf(r.change);
    const st = STATUS_MAP[r.market_status]?.label || r.market_status || '—';
    body.appendChild(el('tr', {}, [
      el('td', {}, fmtDateTimeKST(new Date(r.captured_kst))),
      el('td', {}, [el('span', { class: 'cell-index' }, [
        el('span', { class: 'cell-index__dot', '--accent': `var(${meta.cssVar})` }), meta.short,
      ])]),
      el('td', { class: 'num' }, fmt(r.close, 2)),
      el('td', { class: `num ${dir}` }, `${arrowOf(r.change)} ${signed(r.change, 2)}`),
      el('td', { class: `num ${dir}` }, pct(r.change_pct)),
      el('td', { class: 'num' }, fmt(r.day_high, 2)),
      el('td', { class: 'num' }, fmt(r.day_low, 2)),
      el('td', {}, st),
    ]));
  }
}

/* -------------------------------------------------------------------------- */
/* Banner                                                                     */
/* -------------------------------------------------------------------------- */
function renderBanner() {
  const b = $('#banner');
  if (state.usingFallback) {
    b.hidden = false;
    b.className = 'banner banner--warn';
    b.innerHTML = '<strong>오프라인 미리보기</strong> — 저장소 데이터를 불러오지 못해 내장된 샘플을 표시 중입니다. ' +
      '로컬에서 보려면 <code>python3 -m http.server</code> 로 실행하거나 GitHub Pages에 배포하세요.';
  } else {
    b.hidden = true;
  }
}

/* -------------------------------------------------------------------------- */
/* Orchestration                                                              */
/* -------------------------------------------------------------------------- */
function renderAll() {
  renderBanner();
  renderStatus();
  renderKPIs();
  renderLegend();
  renderComparison();
  renderTable();
}

/* theme */
function applyTheme(mode) {
  document.documentElement.setAttribute('data-theme', mode);
  try { localStorage.setItem('dash-theme', mode); } catch (e) {}
}
function initTheme() {
  let mode = 'auto';
  try { mode = localStorage.getItem('dash-theme') || 'auto'; } catch (e) {}
  // allow ?theme=dark|light|auto to override (shareable links)
  const q = new URLSearchParams(location.search).get('theme');
  if (q === 'dark' || q === 'light' || q === 'auto') { mode = q; applyTheme(mode); }
  document.documentElement.setAttribute('data-theme', mode);
}
function currentEffectiveDark() {
  const m = document.documentElement.getAttribute('data-theme');
  if (m === 'dark') return true;
  if (m === 'light') return false;
  return window.matchMedia('(prefers-color-scheme: dark)').matches;
}

/* resize (re-render charts only) */
let resizeTimer = null;
function onResize() {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (state.latest) renderAll(); }, 160);
}

async function refresh(btn) {
  if (btn) btn.classList.add('is-spinning');
  await loadData();
  renderAll();
  if (btn) setTimeout(() => btn.classList.remove('is-spinning'), 400);
}

function wireControls() {
  $('#theme').addEventListener('click', () => {
    applyTheme(currentEffectiveDark() ? 'light' : 'dark');
    renderAll(); // refresh SVG colors from CSS vars
  });
  $('#refresh').addEventListener('click', (e) => refresh(e.currentTarget.closest('button')));
  $('#range-picker').addEventListener('click', (e) => {
    const btn = e.target.closest('.segmented__btn');
    if (!btn) return;
    state.rangeDays = Number(btn.dataset.days) || 0;
    for (const b of $('#range-picker').children) b.classList.toggle('is-active', b === btn);
    renderKPIs();
    renderComparison();
  });
  window.addEventListener('resize', onResize);
  // re-render when the OS theme flips while in auto mode
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (document.documentElement.getAttribute('data-theme') === 'auto') renderAll();
  });
}

async function init() {
  initTheme();
  wireControls();
  await loadData();
  renderAll();
}
document.addEventListener('DOMContentLoaded', init);

'use strict';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = { config: null, places: [], rating: { placeId: null, stars: 0 }, current: [] };

// ── API ────────────────────────────────────────────────────────────
async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `요청 실패 (${res.status})`);
  return data;
}

const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else if (k === 'html') node.innerHTML = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) node.setAttribute(k, v);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
};

const isMobile = () => /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);

// 모바일에서는 네이버 지도 앱을 먼저 시도하고, 안 열리면 웹으로 넘어간다.
function openNaver(item) {
  if (!isMobile() || !item.app_url) {
    window.open(item.map_url, '_blank', 'noopener');
    return;
  }
  const startedAt = Date.now();
  const fallback = setTimeout(() => {
    if (Date.now() - startedAt < 2000 && !document.hidden) {
      window.open(item.map_url, '_blank', 'noopener');
    }
  }, 900);
  document.addEventListener('visibilitychange', () => clearTimeout(fallback), { once: true });
  window.location.href = item.app_url;
}

const pct = (r) => (r === null || r === undefined ? null : Math.round(r * 100));
const CATEGORY_COLORS = {
  한식: '#ff6b35', 중식: '#f43f5e', 일식: '#2f8fff', 양식: '#8b5cf6', 아시아: '#10b981',
  분식: '#f59e0b', 치킨: '#eab308', 패스트푸드: '#06b6d4', 뷔페: '#ec4899', 기타: '#64748b',
};
const catColor = (major) => CATEGORY_COLORS[major] || CATEGORY_COLORS.기타;

const starText = (avg) => '★'.repeat(Math.round(avg)) + '☆'.repeat(5 - Math.round(avg));

// ── 추천 카드 ──────────────────────────────────────────────────────
// 화면의 다른 카드는 그대로 두고, 이 카드 한 장만 다른 식당으로 바꾼다.
async function replaceCard(item, node, reason) {
  const others = state.current.filter((x) => x.id !== item.id);
  const params = new URLSearchParams({
    count: '1',
    radius: $('#radius-quick').value,
    exclude: [...state.current.map((x) => x.id), item.id].join(','),
    exclude_detail: others.map((x) => x.detail_category).join(','),
    exclude_major: others.map((x) => x.major_category).join(','),
  });
  const data = await api(`/api/recommend?${params}`);
  if (!data.items.length) {
    disableCard(item, node, reason || '제외됨');
    $('#pick-note').textContent = '대신 넣을 만한 다른 분류의 식당이 없습니다. 반경을 넓혀 보세요.';
    return;
  }
  const next = data.items[0];
  state.current = state.current.map((x) => (x.id === item.id ? next : x));
  node.replaceWith(cardFor(next));
  $('#pick-note').textContent = '';
}

// 카드를 비활성 상태로만 바꾼다 (다른 카드는 건드리지 않는다)
function disableCard(item, node, label) {
  node.classList.add('disabled');
  const badge = node.querySelector('.card-state');
  if (badge) badge.textContent = label;
  else node.querySelector('.name').append(el('span', { class: 'card-state' }, label));
  node.querySelectorAll('.row').forEach((r) => r.remove());
  node.append(el('div', { class: 'row' },
    el('button', { onclick: () => restoreCard(item, node) }, '↩︎ 되돌리기'),
    el('button', { class: 'primary', onclick: () => replaceCard(item, node) }, '🔄 다른 곳 보기')));
}

async function restoreCard(item, node) {
  await api(`/api/blocks?place_id=${encodeURIComponent(item.id)}`, { method: 'DELETE' });
  await api('/api/lunch', { method: 'POST', body: { place_id: item.id, open: null } });
  node.replaceWith(cardFor(item));
  loadStats();
}

function cardFor(item) {
  const taste = pct(item.taste_ratio);
  const metrics = [];

  metrics.push(el('div', { class: 'metric' },
    el('span', { class: 'label' }, '거리'),
    el('span', { class: 'value' },
      item.distance_m === null ? '알 수 없음' : `${item.distance_m}m · 도보 ${item.walk_min}분`)));

  if (taste !== null) {
    const n = item.taste_total;
    metrics.push(el('div', { class: 'metric' },
      el('span', { class: 'label' }, '맛있어요'),
      el('span', { class: 'meter' }, el('span', { style: `width:${taste}%` })),
      el('span', { class: 'value' }, `${taste}%`)));
    // 비율만 보면 표본 3명짜리 100% 와 300명짜리 79% 가 같아 보인다. 모수를 같이 적는다.
    metrics.push(el('p', { class: 'sub-metric' },
      n ? `네이버 방문자 리뷰 ${n.toLocaleString('ko-KR')}명 기준` : '리뷰 수 미상',
      n && n < 20 ? el('span', { class: 'thin' }, ' · 표본 적음') : null));
  } else {
    metrics.push(el('div', { class: 'metric' },
      el('span', { class: 'label' }, '맛있어요'),
      el('span', { class: 'muted' }, '지표 없음')));
  }

  metrics.push(el('div', { class: 'metric' },
    el('span', { class: 'label' }, '사내 별점'),
    item.team_count
      ? el('span', { class: 'value' }, `${starText(item.team_avg)} ${item.team_avg} (${item.team_count}명)`)
      : el('span', { class: 'muted' }, '아직 없음')));

  return el('div', { class: 'card' },
    el('p', { class: 'name' },
      el('a', { href: item.map_url, target: '_blank', rel: 'noopener' }, item.name)),
    el('div', { class: 'badges', style: `--cat:${catColor(item.major_category)}` },
      el('span', { class: 'badge' }, item.major_category),
      item.detail_category && item.detail_category !== item.major_category
        ? el('span', { class: 'badge plain' }, item.detail_category) : null),
    el('p', { class: 'muted', style: 'margin:0 0 8px' }, item.address || ''),
    ...metrics,
    item.business_hours ? el('p', { class: 'hours' }, `🕒 ${item.business_hours}`) : null,
    el('p', { class: 'why' }, item.why || ''),
    el('div', { class: 'row' },
      el('button', { class: 'primary', onclick: () => openNaver(item) }, '📍 네이버 지도'),
      item.directions_url
        ? el('a', { class: 'btn', href: item.directions_url, target: '_blank', rel: 'noopener' }, '🚶 길찾기')
        : null),
    el('div', { class: 'row compact' },
      el('button', { onclick: () => openRating(item) }, '⭐ 별점'),
      el('button', { title: '이 자리만 다른 식당으로 교체', onclick: (e) => replaceCard(item, e.target.closest('.card')) }, '🔄 다른 곳'),
      el('button', { title: '점심 장사를 안 하는 곳으로 표시', onclick: (e) => markNoLunch(item, e.target.closest('.card')) }, '🕛 점심 안 함'),
      el('button', { title: '추천 후보에서 영구 제외', onclick: (e) => blockPlace(item, e.target.closest('.card')) }, '🚫 다시 안 보기')));
}

async function draw() {
  const btn = $('#draw');
  btn.disabled = true;
  btn.textContent = '뽑는 중…';
  try {
    const radius = $('#radius-quick').value;
    const count = $('#count-quick').value;
    const data = await api(`/api/recommend?radius=${radius}&count=${count}`);
    $('#pick-note').textContent = data.note || '';
    const cards = $('#cards');
    cards.replaceChildren();
    if (!data.items.length) {
      cards.append(el('p', { class: 'empty' }, '추천할 식당이 없습니다. 설정 탭에서 식당을 먼저 수집하세요.'));
    } else {
      state.current = data.items;
      data.items.forEach((item) => cards.append(cardFor(item)));
    }
  } catch (err) {
    $('#pick-note').textContent = err.message;
  } finally {
    btn.disabled = false;
    btn.textContent = '다시 뽑기';
  }
}

// 영구 제외. 나머지 카드는 그대로 두고 이 카드만 비활성 처리한다.
async function blockPlace(item, node) {
  const reason = prompt(
    `'${item.name}'을(를) 앞으로 추천에서 뺍니다.\n이유(선택) — 예: 폐업, 웨이팅 너무 김, 입맛에 안 맞음`, '');
  if (reason === null) return;
  await api('/api/blocks', {
    method: 'POST',
    body: { place_id: item.id, reason, by: localStorage.getItem('rater') || '' },
  });
  disableCard(item, node, '제외됨');
  loadBlocks(); loadStats();
}

// 점심 장사를 안 하는 곳(저녁만 하는 집 등). 한 번 표시하면 팀 전체에 적용된다.
async function markNoLunch(item, node) {
  if (!confirm(`'${item.name}'은(는) 점심에 영업하지 않나요?\n표시하면 앞으로 추천에서 빠집니다.`)) return;
  await api('/api/lunch', { method: 'POST', body: { place_id: item.id, open: false } });
  disableCard(item, node, '점심 안 함');
  loadStats();
}

// ── 별점 ───────────────────────────────────────────────────────────
async function openRating(item) {
  state.rating = { placeId: item.id, stars: 0 };
  $('#rate-title').textContent = `${item.name} — 별점 남기기`;
  $('#rate-name').value = localStorage.getItem('rater') || '';
  $('#rate-comment').value = '';
  paintStars(0);
  const box = $('#rate-existing');
  box.replaceChildren();
  try {
    const summary = await api(`/api/ratings?place_id=${encodeURIComponent(item.id)}`);
    const mine = summary.ratings.find((r) => r.rater === $('#rate-name').value);
    if (mine) {
      state.rating.stars = mine.stars;
      paintStars(mine.stars);
      $('#rate-comment').value = mine.comment || '';
    }
    summary.ratings.forEach((r) => box.append(
      el('div', {}, `${starText(r.stars)} ${r.rater}${r.comment ? ' — ' + r.comment : ''}`)));
  } catch { /* 아직 별점이 없으면 그냥 빈 목록 */ }
  $('#rate-modal').hidden = false;
}

function paintStars(value) {
  $$('#rate-stars button').forEach((b) => b.classList.toggle('on', Number(b.dataset.v) <= value));
}

async function saveRating() {
  const rater = $('#rate-name').value.trim();
  if (!rater) return alert('이름을 입력해 주세요.');
  if (!state.rating.stars) return alert('별점을 선택해 주세요.');
  localStorage.setItem('rater', rater);
  await api('/api/ratings', {
    method: 'POST',
    body: {
      place_id: state.rating.placeId, rater, stars: state.rating.stars,
      comment: $('#rate-comment').value.trim(),
    },
  });
  $('#rate-modal').hidden = true;
  await Promise.all([loadHistory(), loadPlaces(), loadStats()]);
}

// ── 목록/기록/설정 ─────────────────────────────────────────────────
async function loadHistory() {
  const data = await api('/api/history?limit=20');
  const box = $('#history');
  box.replaceChildren();
  if (!data.items.length) {
    box.append(el('p', { class: 'empty' }, '아직 추천 기록이 없습니다.'));
    return;
  }
  data.items.forEach((batch) => {
    box.append(el('div', { class: 'batch' },
      el('div', { class: 'when' }, `${batch.picked_on} · ${new Date(batch.created_at).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })}`),
      el('ul', {}, batch.items.map((it) => el('li', {},
        `${it.name} (${it.detail_category || it.major_category})`,
        it.team_count ? el('span', { class: 'muted' }, ` ${starText(it.team_avg)} ${it.team_avg}`) : null,
        ' ',
        el('button', {
          style: 'padding:1px 7px;font-size:.75rem',
          onclick: () => openRating({ id: it.place_id, name: it.name }),
        }, '별점'))))));
  });
}

function placeRow(p) {
  const taste = pct(p.taste_ratio);
  return el('div', { class: `place-row${p.blocked || p.lunch_open === 0 ? ' blocked' : ''}` },
    el('span', { class: 'dot', style: `--cat:${catColor(p.major_category)}` }),
    el('span', { class: 'grow' },
      el('div', {}, el('a', { href: p.map_url, target: '_blank', rel: 'noopener', style: 'color:inherit' }, p.name),
        ' ', el('span', { class: 'muted' }, `${p.major_category}${p.detail_category && p.detail_category !== p.major_category ? ' · ' + p.detail_category : ''}`)),
      el('div', { class: 'muted' }, p.address || '')),
    el('span', { class: 'muted', style: 'white-space:nowrap' },
      [p.distance_m !== null ? `${p.distance_m}m` : '',
       taste !== null ? `맛${taste}%` : '',
       p.team_count ? `★${p.team_avg}` : ''].filter(Boolean).join(' · ')),
    el('button', {
      style: 'padding:4px 9px;font-size:.78rem',
      title: p.lunch_open === 0 ? '점심 영업 안 함으로 표시됨' : '점심 영업 여부',
      onclick: async () => {
        await api('/api/lunch', {
          method: 'POST',
          body: { place_id: p.id, open: p.lunch_open === 0 ? true : false },
        });
        await Promise.all([loadPlaces(), loadStats()]);
      },
    }, p.lunch_open === 0 ? '🕛 점심X' : '🕛'),
    el('button', {
      style: 'padding:4px 9px;font-size:.78rem',
      onclick: async () => {
        if (p.blocked) await api(`/api/blocks?place_id=${encodeURIComponent(p.id)}`, { method: 'DELETE' });
        else await api('/api/blocks', { method: 'POST', body: { place_id: p.id, by: localStorage.getItem('rater') || '' } });
        await Promise.all([loadPlaces(), loadBlocks(), loadStats()]);
      },
    }, p.blocked ? '되돌리기' : '제외'));
}


// 이 앱이 스스로 걸어 둔 호출 한도. 콘솔 한도와 별개로 여기서 한 번 더 막는다.
function renderBudget(sel, b, label) {
  const el_ = $(sel);
  if (!b) { el_.hidden = true; return; }
  const daily = b.daily_limit
    ? ` · 오늘 ${b.used_today.toLocaleString()}/${b.daily_limit.toLocaleString()}건`
    : '';
  el_.textContent =
    `${label} 호출 ${b.month} — ${b.used.toLocaleString()}/${b.limit.toLocaleString()}건${daily}`
    + (b.exhausted ? ' · 한도를 다 써서 호출을 멈춘 상태입니다' : '');
  el_.classList.toggle('warn', !!b.exhausted);
  el_.hidden = false;
}

function renderPlaces() {
  const q = $('#place-search').value.trim().toLowerCase();
  const showBlocked = $('#show-blocked').checked;
  const items = state.places.filter((p) => {
    if (!showBlocked && p.blocked) return false;
    if (!q) return true;
    return `${p.name} ${p.major_category} ${p.detail_category} ${p.address}`.toLowerCase().includes(q);
  });
  const box = $('#places');
  box.replaceChildren();
  $('#place-count').textContent = `${items.length}곳`;
  if (!items.length) box.append(el('p', { class: 'empty' }, '표시할 식당이 없습니다.'));
  items.forEach((p) => box.append(placeRow(p)));
}

async function loadPlaces() {
  const data = await api('/api/places?radius=all');
  state.places = data.items;
  renderPlaces();
}

async function loadBlocks() {
  const data = await api('/api/blocks');
  const box = $('#blocks');
  box.replaceChildren();
  $('#block-count').textContent = data.items.length ? `(${data.items.length}곳)` : '';
  if (!data.items.length) {
    box.append(el('p', { class: 'muted' }, '제외된 식당이 없습니다.'));
    return;
  }
  data.items.forEach((b) => box.append(el('div', { class: 'place-row' },
    el('span', { class: 'grow' },
      el('div', {}, b.name, ' ', el('span', { class: 'muted' }, b.detail_category || b.major_category || '')),
      el('div', { class: 'muted' }, [b.reason, b.blocked_by && `by ${b.blocked_by}`].filter(Boolean).join(' · '))),
    el('button', {
      style: 'padding:4px 9px;font-size:.78rem',
      onclick: async () => {
        await api(`/api/blocks?place_id=${encodeURIComponent(b.place_id)}`, { method: 'DELETE' });
        await Promise.all([loadBlocks(), loadPlaces(), loadStats()]);
      },
    }, '되돌리기'))));
}

async function loadStats() {
  const s = await api('/api/stats');
  const box = $('#stats');
  box.replaceChildren();
  const cells = [
    ['반경 내 식당', s.places_in_radius], ['전체 등록', s.places_total],
    ['맛있어요 지표', s.places_with_taste], ['별점 있는 곳', s.rated_places],
    ['제외', s.blocked], ['점심 안 함', s.no_lunch],
  ];
  cells.forEach(([label, value]) => box.append(
    el('div', { class: 'stat' }, el('b', {}, String(value)), el('span', {}, label))));
  s.by_major.slice(0, 6).forEach((row) => box.append(
    el('div', { class: 'stat' }, el('b', {}, String(row.cnt)), el('span', {}, row.major))));
}

async function loadConfig() {
  const cfg = await api('/api/config');
  state.config = cfg;
  $('#subtitle').textContent = `${cfg.office_name} 기준 ${cfg.radius_m}m 이내에서 골라 드립니다.`;
  $('#cfg-office-name').value = cfg.office_name;
  $('#cfg-area').value = cfg.area_keyword || '';
  $('#cfg-lat').value = cfg.office_lat;
  $('#cfg-lng').value = cfg.office_lng;
  $('#cfg-radius').value = cfg.radius_m;
  $('#cfg-count').value = cfg.recommend_count;
  $('#radius-quick').value = [300, 500, 800, 1200].includes(cfg.radius_m) ? cfg.radius_m : 500;
  $('#count-quick').value = [2,3,4,5,6,8,10].includes(cfg.recommend_count) ? cfg.recommend_count : 3;
  const warn = $('#sync-warn');
  const notes = [];
  if (!cfg.has_naver_keys) notes.push('네이버 API 키가 없어 [주변 식당 수집]은 쓸 수 없습니다. 붙여넣기 등록은 그대로 되고 거리만 빕니다.');
  if (!cfg.review_scrape_enabled) notes.push("'맛있어요' 비율 수집이 꺼져 있습니다 (.env 의 ENABLE_PLACE_REVIEW_SCRAPE=1). 공식 API 가 아니라 언제든 막힐 수 있습니다.");
  warn.textContent = notes.join('\n');
  warn.hidden = !notes.length;
  const keyMsg = $('#naver-keys-msg');
  if (cfg.has_naver_keys && !keyMsg.textContent) {
    keyMsg.textContent = cfg.naver_keys_from_env
      ? '키가 .env 에 들어 있습니다. 여기에 넣으면 그 값이 우선합니다.'
      : '키가 저장되어 있습니다.';
  }
  renderBudget('#naver-budget', cfg.naver_budget, '네이버');
  $('#naver-limit-row').hidden = !cfg.naver_budget;
  if (cfg.naver_budget) {
    $('#cfg-naver-month').value = cfg.naver_monthly_call_limit;
    $('#cfg-naver-day').value = cfg.naver_daily_call_limit;
  }
  renderBudget('#google-budget', cfg.google_budget, '구글');
  $('#run-sync').disabled = !cfg.has_naver_keys;
  // 붙여넣기 등록은 키가 없어도 된다 (좌표 없이 상호명·업종만 등록). 미리보기가 켜 준다.
  $('#run-enrich').disabled = !cfg.review_scrape_enabled && !cfg.has_google_key;
}

// ── 수집 진행 폴링 ─────────────────────────────────────────────────
let pollTimer = null;
function pollSync() {
  clearInterval(pollTimer);
  $('#sync-progress').hidden = false;
  pollTimer = setInterval(async () => {
    const s = await api('/api/sync');
    const ratio = s.total ? Math.round((s.done / s.total) * 100) : 0;
    $('#sync-bar').style.width = `${ratio}%`;
    $('#sync-msg').textContent = s.error ? `오류: ${s.error}` : `${s.message} (${s.done}/${s.total})`;
    // 실패는 회색 작은 글씨로 흘려보내지 않는다. 눈에 띄어야 손을 쓸 수 있다.
    $('#sync-msg').classList.toggle('warn', !!s.error);
    $('#sync-msg').classList.toggle('muted', !s.error);
    if (!s.running) {
      clearInterval(pollTimer);
      await Promise.all([loadPlaces(), loadStats(), loadConfig()]);
    }
  }, 900);
}

// ── 초기화 ─────────────────────────────────────────────────────────
function initTabs() {
  $$('.tab').forEach((tab) => tab.addEventListener('click', () => {
    $$('.tab').forEach((t) => t.classList.toggle('active', t === tab));
    $$('.panel').forEach((p) => p.classList.toggle('active', p.id === `panel-${tab.dataset.tab}`));
    if (tab.dataset.tab === 'history') loadHistory();
    if (tab.dataset.tab === 'places') loadPlaces();
    if (tab.dataset.tab === 'admin') { loadBlocks(); loadStats(); loadConfig(); }
  }));
}

function initEvents() {
  $('#draw').addEventListener('click', draw);
  $('#place-search').addEventListener('input', renderPlaces);
  $('#show-blocked').addEventListener('change', renderPlaces);

  $$('#rate-stars button').forEach((b) => b.addEventListener('click', () => {
    state.rating.stars = Number(b.dataset.v);
    paintStars(state.rating.stars);
  }));
  $('#rate-cancel').addEventListener('click', () => { $('#rate-modal').hidden = true; });
  $('#rate-save').addEventListener('click', () => saveRating().catch((e) => alert(e.message)));
  $('#rate-modal').addEventListener('click', (e) => {
    if (e.target.id === 'rate-modal') $('#rate-modal').hidden = true;
  });

  $('#cfg-save').addEventListener('click', async () => {
    await api('/api/config', {
      method: 'POST',
      body: {
        office_name: $('#cfg-office-name').value, area_keyword: $('#cfg-area').value,
        office_lat: $('#cfg-lat').value, office_lng: $('#cfg-lng').value,
        radius_m: $('#cfg-radius').value, recommend_count: $('#cfg-count').value,
      },
    });
    $('#cfg-saved').textContent = '저장했습니다';
    setTimeout(() => { $('#cfg-saved').textContent = ''; }, 2000);
    await loadConfig();
  });

  $('#run-sync').addEventListener('click', async () => {
    try {
      await api('/api/sync', { method: 'POST', body: { area: $('#cfg-area').value } });
      pollSync();
    } catch (e) { alert(e.message); }
  });
  $('#run-enrich').addEventListener('click', async () => {
    try {
      await api('/api/enrich', { method: 'POST', body: { limit: 60 } });
      pollSync();
    } catch (e) { alert(e.message); }
  });

  $('#save-naver-limit').addEventListener('click', async () => {
    try {
      await api('/api/config', {
        method: 'POST',
        body: {
          naver_monthly_call_limit: Number($('#cfg-naver-month').value) || 0,
          naver_daily_call_limit: Number($('#cfg-naver-day').value) || 0,
        },
      });
      await loadConfig();
      $('#naver-limit-msg').textContent = '저장했습니다.';
    } catch (e) { alert(e.message); }
  });

  $('#save-naver-keys').addEventListener('click', async () => {
    const id = $('#naver-id').value.trim();
    const secret = $('#naver-secret').value.trim();
    const msg = $('#naver-keys-msg');
    if (!id || !secret) return alert('Client ID 와 Client Secret 을 모두 넣어 주세요.');
    try {
      const cfg = await api('/api/config', {
        method: 'POST', body: { naver_client_id: id, naver_client_secret: secret },
      });
      $('#naver-secret').value = '';          // 화면에 남겨 두지 않는다
      msg.textContent = '';
      await loadConfig();
      msg.textContent = cfg.has_naver_keys
        ? '저장했습니다. 이제 붙여넣기 등록이 거리까지 채웁니다.'
        : '저장은 됐지만 키가 비어 있습니다. 다시 확인해 주세요.';
    } catch (e) { alert(e.message); }
  });

  $('#reset-lunch-open').addEventListener('click', async () => {
    if (!confirm("'점심 안 함'으로 표시된 식당을 전부 다시 추천 후보로 되돌립니다.\n계속할까요?")) return;
    try {
      const r = await api('/api/lunch-open/reset', { method: 'POST' });
      $('#reset-lunch-msg').textContent = r.restored
        ? `${r.restored}곳을 되돌렸습니다.`
        : "'점심 안 함'으로 표시된 곳이 없습니다.";
      loadPlaces();
    } catch (e) { alert(e.message); }
  });

  // 붙여넣는 대로 상호명을 뽑아 미리 보여 준다
  let previewTimer = null;
  let previewNames = [];

  function importOptions() {
    return {
      drop_cafe: $('#import-drop-cafe').checked,
      drop_pricey: $('#import-drop-pricey').checked,
    };
  }

  async function refreshPreview() {
    const text = $('#import-text').value;
    const box = $('#import-preview');
    if (!text.trim()) {
      box.hidden = true; previewNames = []; $('#run-import').disabled = true;
      $('#import-msg').textContent = '';
      return;
    }
    try {
      const data = await api('/api/import/preview',
        { method: 'POST', body: { text, ...importOptions() } });
      previewNames = data.names;
      $('#run-import').disabled = !data.count;
      const noKeys = data.count && !(state.config && state.config.has_naver_keys);

      // 걸러 낸 곳은 이유별로 묶어서 접어 둔다
      const groups = new Map();
      (data.dropped || []).forEach((d) => {
        if (!groups.has(d.reason)) groups.set(d.reason, []);
        groups.get(d.reason).push(d.name);
      });
      const droppedBox = data.dropped_count
        ? el('details', { class: 'preview-dropped' },
            el('summary', {}, `자동으로 뺀 곳 ${data.dropped_count}곳`),
            ...[...groups.entries()].map(([reason, names]) =>
              el('div', { class: 'preview-group' },
                el('span', { class: 'muted' }, `${reason} ${names.length}곳 — `),
                names.join(', '))))
        : null;

      box.replaceChildren(...[
        el('div', { class: 'preview-head' },
          data.count ? `식당 ${data.count}곳을 찾았습니다` : '상호명을 찾지 못했습니다',
          data.count
            ? el('span', { class: 'muted' }, ' — 아래 목록이 맞으면 등록하세요')
            : el('span', { class: 'muted' }, ' — 네이버 지도 목록을 그대로 붙여넣어 보세요')),
        noKeys
          ? el('p', { class: 'hint', style: 'margin:0 0 8px' },
              '네이버 검색 API 키가 없어 좌표 없이 등록됩니다. 업종은 붙여넣기에서 읽어 오므로 추천은 그대로 됩니다. '
              + '거리로 거르고 싶으면 지도를 회사 중심으로 확대해 원하는 범위만 보이게 한 뒤 그 목록을 붙여넣으세요.')
          : null,
        el('div', { class: 'preview-names' },
          ...data.names.map((n) => el('span', { class: 'pill' }, n))),
        droppedBox,
        data.truncated
          ? el('p', { class: 'warn', style: 'margin:8px 0 0' },
              `한 번에 등록할 수 있는 ${data.max_entries.toLocaleString()}곳을 넘어 `
              + `${data.truncated.toLocaleString()}곳이 잘렸습니다. `
              + '먼저 이만큼 등록한 뒤, 나머지를 다시 붙여넣으세요. 등록은 계속 쌓입니다.')
          : null,
      ].filter(Boolean));
      box.hidden = false;
    } catch (e) {
      box.replaceChildren(el('div', { class: 'warn' }, e.message));
      box.hidden = false;
    }
  }

  $('#import-text').addEventListener('input', () => {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(refreshPreview, 350);
  });
  $('#import-drop-cafe').addEventListener('change', refreshPreview);
  $('#import-drop-pricey').addEventListener('change', refreshPreview);

  $('#run-import').addEventListener('click', async () => {
    const text = $('#import-text').value.trim();
    if (!previewNames.length) return alert('등록할 상호명이 없습니다.');
    if ($('#import-exclusive').checked &&
        !confirm(`${previewNames.length}곳을 등록하고, 이 목록에 없는 기존 식당은 전부 '점심 안 함'으로 표시합니다.\n계속할까요?`)) return;
    try {
      await api('/api/import', {
        method: 'POST',
        body: { text, mark_others_no_lunch: $('#import-exclusive').checked, ...importOptions() },
      });
      const n = previewNames.length;
      // 다음 묶음을 바로 붙여넣을 수 있게 비운다. 등록은 DB 에 쌓이므로 안전하다.
      $('#import-text').value = '';
      $('#import-preview').hidden = true;
      previewNames = [];
      $('#run-import').disabled = true;
      $('#import-msg').textContent = `${n}곳 등록 중…`;
      pollSync();
    } catch (e) { alert(e.message); }
  });

  $('#add-place').addEventListener('click', async () => {
    const [lat, lng] = ($('#add-coord').value || '').split(',').map((v) => parseFloat(v.trim()));
    try {
      await api('/api/places', {
        method: 'POST',
        body: {
          name: $('#add-name').value, address: $('#add-addr').value,
          category: $('#add-cat').value,
          lat: Number.isFinite(lat) ? lat : null, lng: Number.isFinite(lng) ? lng : null,
        },
      });
      $('#add-msg').textContent = '추가했습니다';
      ['#add-name', '#add-addr', '#add-cat', '#add-coord'].forEach((s) => { $(s).value = ''; });
      setTimeout(() => { $('#add-msg').textContent = ''; }, 2000);
      await Promise.all([loadPlaces(), loadStats()]);
    } catch (e) { alert(e.message); }
  });
}

initTabs();
initEvents();
loadConfig().catch((e) => { $('#pick-note').textContent = e.message; });

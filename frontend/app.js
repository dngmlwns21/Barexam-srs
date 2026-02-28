'use strict';

// ── Constants ─────────────────────────────────────────────────────────────────
const API = '/api/v1';

// ── Feature 4: Citation Regex Parser ─────────────────────────────────────────
//
// Wraps detected Korean legal citations in clickable buttons that open the
// Mini Legal Dictionary (Feature 2) with the citation pre-populated.
//
// Patterns matched (in priority order — single-pass combined regex):
//   1. Supreme Court cases:  대법원 2018. 3. 25. 선고 2017다1234 판결
//   2. Constitutional Court: 헌법재판소 2020. 9. 24. 선고 2017헌바123 결정
//   3. Named law + article:  민법 제390조 제2항 제1호
//   4. Bare article ref:     제390조의2 제1항
const _CITATION_RE = new RegExp(
  '(' +
  // SC case
  '대법원\\s*\\d{4}\\.\\s*\\d{1,2}\\.\\s*\\d{1,2}\\.\\s*(?:선고|자)\\s*[\\d가-힣]+\\s*(?:판결|결정|전원합의체\\s*판결)' +
  '|' +
  // CC decision
  '헌법재판소\\s*\\d{4}\\.\\s*\\d{1,2}\\.\\s*\\d{1,2}\\.\\s*(?:선고|자)\\s*[\\d가-힣]+\\s*(?:결정|헌결)' +
  '|' +
  // Named law + article
  '(?:민법|형법|상법|헌법|행정기본법|행정소송법|행정절차법|국가배상법|국세기본법|' +
  '민사소송법|형사소송법|민사집행법|채무자\\s*회생\\s*및\\s*파산에\\s*관한\\s*법률|' +
  '국제사법|상속세\\s*및\\s*증여세법|부동산등기법|공탁법)\\s*제\\d+조(?:의\\d+)?(?:\\s*제\\d+항)?(?:\\s*제\\d+호)?' +
  '|' +
  // Bare article (only when standalone — not preceded by a letter or digit)
  '(?<![가-힣\\d])제\\d+조(?:의\\d+)?(?:\\s*제\\d+항)?(?:\\s*제\\d+호)?' +
  ')',
  'g'
);

/**
 * Wrap citation patterns inside already-escaped HTML with clickable buttons.
 * Must be called AFTER esc()/fmt() so we're working on final HTML.
 */
function linkCitations(html) {
  if (!html) return html;
  return html.replace(_CITATION_RE, (match) => {
    const encoded = match.replace(/"/g, '&quot;');
    return `<button class="citation-link" onclick="openCitationDict('${encoded}')">${match}</button>`;
  });
}

/** fmt() + citation linking — use this wherever explanation text is rendered. */
function fmtCite(text) {
  return linkCitations(fmt(text));
}

// ── Feature 2: Mini Legal Dictionary ─────────────────────────────────────────

function openCitationDict(query) {
  showMiniDict(query);
}

function showMiniDict(query) {
  const panel = document.getElementById('mini-dict-panel');
  if (!panel) return;
  panel.classList.add('open');
  if (query) {
    const input = document.getElementById('mini-dict-input');
    if (input) {
      input.value = query;
      performDictSearch(query);
    }
  }
}

function closeMiniDict() {
  document.getElementById('mini-dict-panel')?.classList.remove('open');
}

async function performDictSearch(q) {
  q = (q || '').trim();
  if (!q) return;
  const resultsEl = document.getElementById('mini-dict-results');
  if (!resultsEl) return;
  resultsEl.innerHTML = '<div class="dict-loading">🔍 검색 중…</div>';
  try {
    const data = await api.get(
      `/dictionary/search?q=${encodeURIComponent(q)}&type=${S.dictType}`
    );
    if (!data || data.length === 0) {
      resultsEl.innerHTML = '<div class="dict-empty">검색 결과가 없습니다.</div>';
      return;
    }
    resultsEl.innerHTML = data.map(item => `
      <div class="dict-result-item">
        <div class="dict-result-type ${esc(item.type)}">${
          item.type === 'statute' ? '📖 법령' : '⚖️ 판례'
        }</div>
        ${item.subject ? `<div class="dict-result-subject">${esc(item.subject)}</div>` : ''}
        <div class="dict-result-title">${esc(item.title)}</div>
        ${item.snippet ? `<div class="dict-result-snippet">${esc(item.snippet)}</div>` : ''}
        ${item.date ? `<div class="dict-result-snippet">시행일: ${esc(item.date)}</div>` : ''}
        ${item.url ? `<a class="dict-result-link" href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">원문 보기 →</a>` : ''}
      </div>
    `).join('');
  } catch (err) {
    resultsEl.innerHTML = `<div class="dict-error-msg">오류: ${esc(err.message)}</div>`;
  }
}

// ── Token helpers ─────────────────────────────────────────────────────────────
const tokens = {
  get access()  { return localStorage.getItem('access_token'); },
  get refresh() { return localStorage.getItem('refresh_token'); },
  set(tokenOut) {
    localStorage.setItem('access_token',  tokenOut.access_token);
    if (tokenOut.refresh_token) {
      localStorage.setItem('refresh_token', tokenOut.refresh_token);
    }
  },
  clear() {
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
  },
  exists() { return !!localStorage.getItem('access_token'); },
};

// ── API layer ─────────────────────────────────────────────────────────────────
let _refreshing = false;

const api = {
  async request(method, path, body, retry = true) {
    const headers = { 'Content-Type': 'application/json' };
    if (tokens.access) headers['Authorization'] = `Bearer ${tokens.access}`;

    const opts = { method, headers };
    if (body !== undefined) opts.body = JSON.stringify(body);

    const r = await fetch(`${API}${path}`, opts);

    if (r.status === 401 && retry && !_refreshing) {
      // Try token refresh
      const refreshed = await api._tryRefresh();
      if (refreshed) {
        return api.request(method, path, body, false); // retry once
      }
      // Refresh failed → go to login
      tokens.clear();
      showLogin();
      throw new Error('Session expired');
    }

    if (!r.ok) {
      let msg = `${method} ${path} → ${r.status}`;
      try {
        const detail = await r.json();
        if (detail.detail) msg = detail.detail;
      } catch (_) {}
      throw new Error(msg);
    }

    if (r.status === 204) return null;
    return r.json();
  },

  async _tryRefresh() {
    const rt = tokens.refresh;
    if (!rt) return false;
    _refreshing = true;
    try {
      const r = await fetch(`${API}/auth/refresh?refresh_token=${encodeURIComponent(rt)}`, {
        method: 'POST',
      });
      if (!r.ok) return false;
      const data = await r.json();
      tokens.set(data);
      return true;
    } catch (_) {
      return false;
    } finally {
      _refreshing = false;
    }
  },

  get(path)        { return api.request('GET',  path); },
  post(path, body) { return api.request('POST', path, body); },
  put(path, body)  { return api.request('PUT',  path, body); },
  getMockTest(numCards = 20) {
    return api.get(`/mock/mock-test?num_cards=${numCards}`);
  },
};

// ── State ─────────────────────────────────────────────────────────────────────
const S = {
  screen:         'login',  // 'login' | 'home' | 'studylist' | 'study' | 'result' | 'done' | 'mypage' | 'quickscan'
  activeTab:      'home',   // 'home' | 'study' | 'mypage'
  deckStats:      [],       // DeckStatsOut[]
  myPageTab:      'stats',  // 'stats' | 'history' | 'bookmarks'
  returnTo:       null,     // null | 'mypage' — where to go after single-card restudy
  subjects:       [],       // SubjectOut[]
  stats:          null,     // OverallStatsOut
  subjectStats:   [],       // SubjectStatsOut[]
  activeSubjectId: null,    // UUID string | null
  card:           null,     // DueCardOut (full object)
  chosen:         null,     // choice_number (int) or 'O'/'X' for choice_ox
  revealData:     null,     // { answer, explanation }
  peerStats:      null,     // QuestionStatsOut
  sessionDone:    0,
  streak:         0,
  user:           null,     // UserOut (from /users/me)
  // FIX M-5: time tracking
  cardShownAt:    null,     // timestamp (ms) when current card was first displayed
  // FIX M-3: undo support
  undoBuffer:     null,     // { flashcardId, timer } — pending undo within 8s window
  // history pagination
  historyOffset:  0,
  historyHasMore: false,
  // quick-scan state
  qsCards:        [],
  qsIdx:          0,
  qsFlipped:      false,
  qsMode:         'failure',
  // mock OX study mode
  mockDeckStats:  [],   // DeckOut[] from /api/v1/mock/decks
  isMockMode:     false,
  mockQueue:      [],   // OXCardOut[] (shuffled)
  mockIdx:        0,
  mockTest:       null, // { cards, index, answers, startTime, timeLimit }
  // search
  searchQuery:    '',
  searchResults:  [],
  searchSubjectId: null,
  // history filter
  wrongOnlyFilter: false,
  historySubjectId: null,
  // weekly stats
  weeklyStats:    null,
  // mini dict type filter
  dictType:       'all',  // 'all' | 'statute' | 'precedent'
};

// ── Escape / format helpers ───────────────────────────────────────────────────
function esc(str) {
  if (!str && str !== 0) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function fmt(text) {
  return esc(text)
    .replace(/\n/g, '<br>')
    .replace(/&lt;mark&gt;/g, '<mark>')
    .replace(/&lt;\/mark&gt;/g, '</mark>');
}

// ── SM-2 helpers ──────────────────────────────────────────────────────────────
function calcNextInterval(sm2, rating, user) {
  const hardMin  = (user && user.sm2_hard_interval_minutes) || 10;
  const goodDays = (user && user.sm2_good_interval_days)    || 1;
  const easyDays = (user && user.sm2_easy_interval_days)    || 3;
  const iv  = (sm2 && sm2.interval_days) || 1;
  const ef  = (sm2 && sm2.ease_factor)   || 2.5;
  const rep = (sm2 && sm2.repetitions)   || 0;

  if (rating <= 2) return hardMin / 1440;
  if (rating === 3) {
    if (rep === 0) return goodDays;
    if (rep === 1) return goodDays * 3;
    return Math.round(iv * ef * 10000) / 10000;
  }
  // rating >= 4 (Easy / Perfect)
  if (rep === 0) return easyDays;
  if (rep === 1) return easyDays * 4;
  return Math.round(iv * ef * 1.3 * 10000) / 10000;
}

function fmtInterval(days) {
  if (days < 1 / 24) return `${Math.round(days * 1440)}분`;
  if (days < 1)      return `${Math.round(days * 24)}시간`;
  if (days < 7)      return `${Math.round(days)}일`;
  if (days < 30)     return `${Math.round(days / 7)}주`;
  return `${Math.round(days / 30)}개월`;
}

// ── Mock OX card adapter ──────────────────────────────────────────────────────
function _mockCardToDue(oxCard) {
  return {
    flashcard_id: `mock-${oxCard.raw_id}-${oxCard.letter}`,
    type: 'choice_ox',
    is_starred: false,
    personal_note: null,
    sm2: { interval_days: 1, ease_factor: 2.5, repetitions: 0 },
    question: {
      id: `${oxCard.raw_id}-${oxCard.letter}`,
      stem: '',  // OX statements are self-contained — no garbled PDF stem
      explanation: oxCard.explanation,
      overall_explanation: oxCard.overall_explanation, // New field
      keywords: [], // OXCard의 keywords는 선택지별 키워드이므로, 문제 전체 키워드는 비워둡니다.
      is_outdated: oxCard.is_outdated,
      needs_revision: oxCard.is_revised,
      source_name: oxCard.source,
      source_year: oxCard.year,
      question_number: oxCard.question_number,
      tags: [oxCard.subject],
      choices: [],
    },
    choice: {
      id: `${oxCard.raw_id}-${oxCard.letter}`,
      content: oxCard.statement,  // clean LLM-rewritten standalone statement
      is_correct: oxCard.is_correct,
      choice_number: oxCard.choice_number,
      legal_basis: oxCard.legal_basis, // New field
      case_citation: oxCard.case_citation, // New field
      explanation_core: oxCard.explanation_core, // New field
      keywords: oxCard.keywords, // New field
    },
    _ox: oxCard, // keep original for rich result display
  };
}

// ── Dark mode ─────────────────────────────────────────────────────────────────
function toggleDarkMode() {
  // FIX M-8: toggle on both html (for flash-prevention) and body (for CSS vars)
  const dark = document.body.classList.toggle('dark-mode');
  document.documentElement.classList.toggle('dark-mode', dark);
  localStorage.setItem('dark_mode', dark ? '1' : '0');
  document.querySelectorAll('.btn-dark-toggle').forEach(btn => {
    btn.textContent = dark ? '🌙' : '☀️';
  });
}

// ── Screen container helpers ──────────────────────────────────────────────────
function showLoading() {
  document.getElementById('loading-screen').hidden = false;
  document.getElementById('login-screen').hidden   = true;
  document.getElementById('dynamic-screen').innerHTML = '';
}

function hideLoading() {
  document.getElementById('loading-screen').hidden = true;
}

// ── Bottom nav helpers ────────────────────────────────────────────────────────
function showBottomNav() {
  document.getElementById('bottom-nav').hidden = false;
}

function hideBottomNav() {
  document.getElementById('bottom-nav').hidden = true;
}

function setActiveTab(tab) {
  S.activeTab = tab;
  document.querySelectorAll('.nav-tab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tab);
  });
}

// ── LOGIN / REGISTER ──────────────────────────────────────────────────────────
let _authMode = 'login'; // 'login' | 'register'

function showLogin() {
  S.screen = 'login';
  hideBottomNav();
  document.getElementById('login-screen').hidden   = false;
  document.getElementById('dynamic-screen').innerHTML = '';
  document.getElementById('loading-screen').hidden = true;
  renderLogin();
}

function renderLogin() {
  // Tab state
  const isRegister = _authMode === 'register';
  document.getElementById('tab-login').classList.toggle('active', !isRegister);
  document.getElementById('tab-register').classList.toggle('active', isRegister);

  // Show/hide name field
  document.getElementById('name-group').hidden = !isRegister;

  // Button label
  document.getElementById('btn-auth-submit').textContent = isRegister ? '회원가입' : '로그인';

  // Password autocomplete hint
  document.getElementById('auth-password').autocomplete = isRegister ? 'new-password' : 'current-password';

  // Clear error
  document.getElementById('auth-error').textContent = '';
}

async function handleAuthSubmit(e) {
  e.preventDefault();
  const email    = document.getElementById('auth-email').value.trim();
  const password = document.getElementById('auth-password').value;
  const name     = document.getElementById('auth-name').value.trim();
  const errEl    = document.getElementById('auth-error');
  const btn      = document.getElementById('btn-auth-submit');

  errEl.textContent = '';
  btn.disabled = true;

  try {
    let data;
    if (_authMode === 'login') {
      data = await api.post('/auth/login', { email, password });
    } else {
      data = await api.post('/auth/register', {
        email,
        password,
        display_name: name || undefined,
      });
    }
    tokens.set(data);
    await showHome();
  } catch (err) {
    errEl.textContent = err.message || '오류가 발생했습니다.';
  } finally {
    btn.disabled = false;
  }
}

function logout() {
  tokens.clear();
  _authMode = 'login';
  showLogin();
}

// ── HOME ──────────────────────────────────────────────────────────────────────
async function showHome() {
  S.screen     = 'home';
  S.card       = null;
  S.chosen     = null;
  S.revealData = null;
  S.mockTest   = null;

  showLoading();
  document.getElementById('login-screen').hidden = true;

  try {
    const [stats, subjStats, subjects, user, mockDecks] = await Promise.all([
      api.get('/stats/'),
      api.get('/stats/subjects'),
      api.get('/subjects/'),
      api.get('/users/me'),
      fetch(`${API}/mock/decks`).then(r => r.json()).catch(() => []),
    ]);
    S.stats          = stats;
    S.subjectStats   = subjStats;
    S.subjects       = subjects;
    S.streak         = stats.study_streak;
    S.user           = user;
    S.mockDeckStats  = mockDecks || [];
  } catch (err) {
    console.error(err);
    hideLoading();
    // Show error on screen (visible on mobile too)
    document.getElementById('dynamic-screen').innerHTML = `
      <div style="padding:2rem;text-align:center;">
        <p style="color:#c00;font-weight:bold;font-size:1rem;">홈 화면 로드 실패</p>
        <pre style="margin:1rem 0;padding:1rem;background:#f5f5f5;border-radius:8px;
                    font-size:.75rem;text-align:left;white-space:pre-wrap;word-break:break-all;">
${esc(err.message)}</pre>
        <button onclick="showLogin()"
                style="padding:.6rem 1.4rem;background:#1a73e8;color:#fff;
                       border:none;border-radius:8px;cursor:pointer;font-size:.9rem;">
          로그인으로 돌아가기
        </button>
      </div>
    `;
    // If unauthorized, showLogin was already called inside api.request
    return;
  }

  showBottomNav();
  setActiveTab('home');
  hideLoading();
  renderHome();
}

function renderHome() {
  const { stats, streak, mockDeckStats } = S;
  const total    = stats.due_today + stats.reviewed_today;
  const pct      = total > 0 ? Math.round((stats.reviewed_today / total) * 100) : 0;

  // Stats strip
  const statsStrip = `
    <div class="stats-strip">
      <div class="stat-cell">
        <div class="stat-cell-value">${stats.total_cards.toLocaleString()}</div>
        <div class="stat-cell-label">전체</div>
      </div>
      <div class="stat-cell">
        <div class="stat-cell-value" style="color:var(--primary)">${stats.due_today.toLocaleString()}</div>
        <div class="stat-cell-label">예정</div>
      </div>
      <div class="stat-cell">
        <div class="stat-cell-value" style="color:var(--success)">${stats.reviewed_today.toLocaleString()}</div>
        <div class="stat-cell-label">완료</div>
      </div>
      <div class="stat-cell">
        <div class="stat-cell-value" style="color:var(--warning)">${stats.accuracy_7d.toFixed(0)}%</div>
        <div class="stat-cell-label">7일 정확도</div>
      </div>
    </div>
  `;

  const quickScanSection = `
    <div class="qs-section-header" style="margin: 24px 0 8px;">⚡ 빠른 복습 (시험 직전 모드)</div>
    <div class="qs-mode-cards">
      <button class="qs-mode-btn" data-mode="failure">📉<span>오답 집중</span><small>최근에 틀린 문제</small></button>
      <button class="qs-mode-btn" data-mode="newest">🆕<span>최신 문제</span><small>최근 추가된 문제</small></button>
      <button class="qs-mode-btn" data-mode="favorites">⭐<span>즐겨찾기</span><small>별표 표시 카드</small></button>
    </div>
  `;

  // OX Deck table (from mock API)
  function deckRow(subject, n, lrn, rev, isOverall) {
    return `
      <button class="deck-row${isOverall ? ' deck-row-overall' : ''}" data-mock-subject="${esc(subject)}">
        <span class="deck-name">${esc(subject)}</span>
        <span class="deck-counts">
          <span class="deck-count deck-new"  title="신규">${n}</span>
          <span class="deck-count deck-learn" title="학습중">${lrn}</span>
          <span class="deck-count deck-rev"  title="복습">${rev}</span>
        </span>
      </button>`;
  }

  let deckSection;
  if (mockDeckStats.length > 0) {
    const totalNew = mockDeckStats.reduce((a, d) => a + d.new_count, 0);
    const totalLrn = mockDeckStats.reduce((a, d) => a + d.learning_count, 0);
    const totalRev = mockDeckStats.reduce((a, d) => a + d.review_count, 0);
    deckSection = `
      <div class="deck-table">
        <div class="deck-table-header">
          <span class="deck-header-name">OX 카드</span>
          <span class="deck-header-counts">
            <span class="deck-count deck-new"  title="신규">신규</span>
            <span class="deck-count deck-learn" title="학습중">학습</span>
            <span class="deck-count deck-rev"  title="복습">복습</span>
          </span>
        </div>
        ${deckRow('전체 OX 카드', totalNew, totalLrn, totalRev, true)}
        ${mockDeckStats.map(d => deckRow(d.subject, d.new_count, d.learning_count, d.review_count, false)).join('')}
      </div>
      <div class="deck-legend">
        <span><span class="legend-dot legend-new"></span>신규</span>
        <span><span class="legend-dot legend-learn"></span>학습중</span>
        <span><span class="legend-dot legend-rev"></span>복습</span>
        <span class="legend-acc">7일 정확도 ${stats.accuracy_7d.toFixed(1)}%</span>
      </div>
    `;
  } else {
    deckSection = `
      <button class="btn-cta" id="btn-cta">OX 카드 학습 시작</button>
    `;
  }

  const dynEl = document.getElementById('dynamic-screen');
  dynEl.innerHTML = `
    <div class="home-v2">
      <div class="home-topbar">
        <span class="home-topbar-title">⚖️ 변호사시험 SRS</span>
        <div class="home-topbar-actions">
          <button class="btn-dark-toggle" id="btn-dark-toggle-home" title="다크 모드">
            ${document.body.classList.contains('dark-mode') ? '🌙' : '☀️'}
          </button>
          <button class="btn-logout" id="btn-logout">로그아웃</button>
        </div>
      </div>

      <div class="hero-card">
        <div class="hero-streak-label">연속 학습</div>
        <div class="hero-streak-value">${streak > 0 ? `🔥 ${streak}일 연속` : '오늘 시작해요!'}</div>
      </div>

      ${statsStrip}
      ${quickScanSection}
      ${deckSection}

      <div class="mock-test-section">
        <button class="btn-mock-setup" id="btn-mock-setup">📝 셀프 모의고사</button>
      </div>
    </div>
  `;

  document.getElementById('btn-logout').addEventListener('click', logout);
  document.getElementById('btn-dark-toggle-home').addEventListener('click', toggleDarkMode);
  document.getElementById('btn-mock-setup').addEventListener('click', showMockSetup);
  document.getElementById('btn-cta')?.addEventListener('click', () => startMockStudy(null));
  document.querySelectorAll('.deck-row').forEach(btn => {
    btn.addEventListener('click', () => {
      const subj = btn.dataset.mockSubject;
      startMockStudy(subj === '전체 OX 카드' ? null : subj);
    });
  });
  document.querySelectorAll('.qs-mode-btn').forEach(btn => {
    btn.addEventListener('click', () => showQuickScan(btn.dataset.mode));
  });
}

// ── STUDY LIST ────────────────────────────────────────────────────────────────
async function showStudyList() {
  S.screen = 'studylist';
  showBottomNav();
  setActiveTab('study');
  showLoading();
  document.getElementById('login-screen').hidden = true;

  try {
    const [stats, subjStats, subjects] = await Promise.all([
      api.get('/stats/'),
      api.get('/stats/subjects'),
      api.get('/subjects/'),
    ]);
    S.stats        = stats;
    S.subjectStats = subjStats;
    S.subjects     = subjects;
    S.streak       = stats.study_streak;
  } catch(e) {
    console.error(e);
    hideLoading();
    return;
  }
  hideLoading();
  renderStudyList();
}

function renderStudyList() {
  const { subjects, subjectStats, stats } = S;

  function makeCard(id, name, due, total, reviewed) {
    const todayDone  = due + reviewed > 0 ? reviewed : 0;
    const todayTotal = due + reviewed;
    const pct = todayTotal > 0 ? Math.round((todayDone / todayTotal) * 100) : (due === 0 ? 100 : 0);
    return `
      <button class="subject-card-v2" data-id="${esc(id)}">
        <div class="subject-card-v2-top">
          <div class="subject-card-v2-name">${esc(name)}</div>
          <div class="subject-card-v2-badge ${due > 0 ? 'has-due' : 'no-due'}">
            ${due > 0 ? `${due.toLocaleString()} 예정` : '완료'}
          </div>
        </div>
        <div class="subject-progress">
          <div class="subject-progress-fill" style="width:${pct}%"></div>
        </div>
        <div class="subject-card-v2-footer">
          <span>${total.toLocaleString()} 카드</span>
          <span>오늘 ${reviewed.toLocaleString()} 완료</span>
        </div>
      </button>
    `;
  }

  const overallCard  = makeCard('', '전체 과목', stats.due_today, stats.total_cards, stats.reviewed_today);
  const subjectCards = subjects.map(s => {
    const ss       = subjectStats.find(x => x.subject_id === s.id);
    const due      = ss ? ss.due : 0;
    const total    = ss ? ss.total : (s.total_questions || 0);
    const reviewed = ss ? ss.reviewed_today : 0;
    return makeCard(s.id, s.name, due, total, reviewed);
  }).join('');

  const dynEl = document.getElementById('dynamic-screen');
  dynEl.innerHTML = `
    <div class="study-list-view">
      <div class="section-header">
        <h2 class="section-title">📚 과목별 학습</h2>
      </div>
      <div style="margin-bottom:16px;">${overallCard}</div>
      ${subjectCards}
    </div>
  `;

  document.querySelectorAll('.subject-card-v2').forEach(btn => {
    btn.addEventListener('click', () => {
      S.activeSubjectId = btn.dataset.id || null;
      startStudy();
    });
  });
}

// ── STUDY ─────────────────────────────────────────────────────────────────────
async function startStudy() {
  S.isMockMode  = false;
  S.returnTo    = null;
  S.sessionDone = 0;
  await fetchNextCard();
}

async function startMockStudy(subject) {
  S.isMockMode  = true;
  S.returnTo    = null;
  S.sessionDone = 0;
  hideBottomNav();
  showLoading();
  document.getElementById('login-screen').hidden = true;
  try {
    const url = subject
      ? `${API}/mock/cards?subject=${encodeURIComponent(subject)}&limit=500`
      : `${API}/mock/cards?limit=500`;
    const cards = await fetch(url).then(r => r.json());
    // Fisher-Yates shuffle
    for (let i = cards.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [cards[i], cards[j]] = [cards[j], cards[i]];
    }
    S.mockQueue = cards;
    S.mockIdx   = 0;
    if (S.mockQueue.length === 0) {
      hideLoading();
      S.screen = 'done';
      renderDone();
      return;
    }
    S.card       = _mockCardToDue(S.mockQueue[0]);
    S.chosen     = null;
    S.revealData = null;
    S.peerStats  = null;
    S.screen     = 'study';
    hideLoading();
    renderStudy();
  } catch (err) {
    console.error(err);
    hideLoading();
  }
}

async function studySpecificCard(flashcardId) {
  S.returnTo = 'mypage';
  hideBottomNav();
  showLoading();
  document.getElementById('login-screen').hidden = true;
  try {
    const card = await api.get(`/flashcards/${flashcardId}`);
    S.card       = card;
    S.chosen     = null;
    S.revealData = null;
    S.peerStats  = null;
    S.screen     = 'study';
    hideLoading();
    renderStudy();
  } catch(e) {
    console.error(e);
    hideLoading();
    showBottomNav();
    setActiveTab('mypage');
  }
}

async function fetchNextCard() {
  // ── Mock mode: advance queue locally, no API call ──────────────────────────
  if (S.isMockMode) {
    S.mockIdx++;
    if (S.mockIdx >= S.mockQueue.length) {
      S.screen = 'done';
      renderDone();
      return;
    }
    S.card       = _mockCardToDue(S.mockQueue[S.mockIdx]);
    S.chosen     = null;
    S.revealData = null;
    S.peerStats  = null;
    S.screen     = 'study';
    renderStudy();
    return;
  }

  hideBottomNav();
  showLoading();
  document.getElementById('login-screen').hidden = true;

  try {
    let path = '/flashcards/due?limit=1';
    if (S.activeSubjectId) path += `&subject_id=${encodeURIComponent(S.activeSubjectId)}`;

    const cards = await api.get(path);

    if (!cards || cards.length === 0) {
      hideLoading();
      S.screen = 'done';
      renderDone();
      return;
    }

    S.card       = cards[0];   // DueCardOut
    S.chosen     = null;
    S.revealData = null;
    S.peerStats  = null;
    S.screen     = 'study';
    hideLoading();
    renderStudy();
  } catch (err) {
    console.error(err);
    hideLoading();
  }
}

function renderStudy() {
  S.cardShownAt = Date.now(); // FIX M-5: start timer for time_spent_ms
  const card = S.card;
  const q    = card.question;
  const isOX = card.type === 'choice_ox';

  // Warning badge
  let warningBadge = '';
  if (q.is_outdated) {
    warningBadge = `<div class="warning-badge warning-outdated">⚠️ 출제 당시 법령 — 현행 법령과 다를 수 있습니다</div>`;
  } else if (q.needs_revision) {
    warningBadge = `<div class="warning-badge warning-revision">✏️ 개정 검토 필요 문항입니다</div>`;
  }

  // Choices / O/X section
  let choicesSection;
  if (isOX) {
    const c = card.choice;
    choicesSection = `
      <div class="ox-statement">
        <div class="ox-statement-label">다음 지문이 맞으면 O, 틀리면 X를 선택하세요</div>
        <div class="ox-statement-text">${fmt(c.content)}</div>
      </div>
      <div class="ox-buttons">
        <button class="btn-ox btn-ox-o" id="btn-ox-o">O<small>맞음</small></button>
        <button class="btn-ox btn-ox-x" id="btn-ox-x">X<small>틀림</small></button>
      </div>
      <div class="keyboard-hint">키보드: O = 맞음 &nbsp;|&nbsp; X = 틀림</div>
    `;
  } else {
    choicesSection = `
      <div class="ox-statement">
        <div class="ox-statement-label">정답을 알면 O, 모르면 X를 선택하세요</div>
        <div class="ox-statement-text">${fmt(q.stem)}</div>
      </div>
      <div class="ox-buttons">
        <button class="btn-ox btn-ox-o" id="btn-ox-o">O<small>알아요</small></button>
        <button class="btn-ox btn-ox-x" id="btn-ox-x">X<small>모르겠어요</small></button>
      </div>
      <div class="keyboard-hint">키보드: O = 알아요 &nbsp;|&nbsp; X = 모르겠어요</div>
    `;
  }

  const dynEl = document.getElementById('dynamic-screen');
  dynEl.innerHTML = `
    <div class="study">
      <div class="study-header">
        <button class="btn-back" id="btn-back">← 홈</button>
        <div class="study-meta">
          ${isOX ? '<span class="ox-label">O/X</span>' : ''}
          ${esc(q.source_name || '')}${q.source_year ? ` ${q.source_year}년` : ''}${q.question_number ? ` · ${q.question_number}번` : ''}
        </div>
        <div style="display:flex;align-items:center;gap:8px;">
          <button class="btn-star${card.is_starred ? ' starred' : ''}" id="btn-star" title="즐겨찾기">
            ${card.is_starred ? '★' : '☆'}
          </button>
          <button class="btn-help" id="btn-help" title="키보드 단축키 (?)">?</button>
          <div class="session-count">${S.sessionDone}개 완료</div>
        </div>
      </div>

      <div class="question-card">
        ${warningBadge}
        ${card.personal_note ? `<div class="study-note-banner">📝 ${esc(card.personal_note)}</div>` : ''}
        ${q.stem ? `<div class="question-text">${fmt(q.stem)}</div>` : ''}
        ${choicesSection}
      </div>
    </div>
  `;

  document.getElementById('btn-back').addEventListener('click',
    S.returnTo === 'mypage' ? () => showMyPage('history') : showHome);
  document.getElementById('btn-star').addEventListener('click', () => toggleStar(q.id, card.is_starred));
  document.getElementById('btn-help')?.addEventListener('click', showHelpModal);

  document.getElementById('btn-ox-o').addEventListener('click', () => selectOX('O'));
  document.getElementById('btn-ox-x').addEventListener('click', () => selectOX('X'));
}

async function selectChoice(num) {
  if (S.chosen !== null) return; // guard double-click
  S.chosen = num;

  document.querySelectorAll('.choice').forEach(b => (b.disabled = true));

  // Immediate reveal using embedded card data — no API round-trip needed
  S.revealData = {
    answer:      S.card.question.correct_choice,
    explanation: S.card.question.explanation,
  };
  S.peerStats = null;
  S.screen = 'result';
  renderResult();

  // Fetch peer stats in background, update section in-place
  try {
    const questionId = S.card.question.id;
    S.peerStats = await api.get(`/questions/${questionId}/stats`);
    const peerEl = document.getElementById('peer-stats-section');
    if (peerEl && S.screen === 'result') {
      const ps = S.peerStats;
      peerEl.innerHTML = ps && ps.total_attempts > 0 ? `
        <div class="peer-stats">
          <span class="peer-icon">👥</span>
          전체 정답률 <strong>${ps.difficulty_pct}%</strong>
          <span class="peer-total">(${ps.total_attempts.toLocaleString()}명 응답)</span>
        </div>
      ` : '';
    }
  } catch (err) {
    console.error('Peer stats fetch failed:', err);
  }
}

// ── O/X card selection ────────────────────────────────────────────────────────
async function selectOX(answer) {
  if (S.chosen !== null) return;
  S.chosen = answer; // 'O' or 'X'

  document.querySelectorAll('.btn-ox').forEach(b => (b.disabled = true));

  // Immediate reveal
  if (S.card.type === 'choice_ox') {
    S.revealData = {
      answer:      S.card.choice.is_correct ? 'O' : 'X',
      explanation: S.card.question.explanation,
    };
  } else {
    // MCQ self-assess: reveal correct choice for display
    S.revealData = {
      answer:      S.card.question.correct_choice,
      explanation: S.card.question.explanation,
    };
  }
  S.peerStats = null;
  S.screen = 'result';
  renderResult();

  // Fetch peer stats in background, update section in-place
  try {
    const questionId = S.card.question.id;
    S.peerStats = await api.get(`/questions/${questionId}/stats`);
    const peerEl = document.getElementById('peer-stats-section');
    if (peerEl && S.screen === 'result') {
      const ps = S.peerStats;
      peerEl.innerHTML = ps && ps.total_attempts > 0 ? `
        <div class="peer-stats">
          <span class="peer-icon">👥</span>
          전체 정답률 <strong>${ps.difficulty_pct}%</strong>
          <span class="peer-total">(${ps.total_attempts.toLocaleString()}명 응답)</span>
        </div>
      ` : '';
    }
  } catch (err) {
    console.error('Peer stats fetch failed:', err);
  }
}

// ── RESULT ────────────────────────────────────────────────────────────────────
function renderResult() {
  const card  = S.card;
  const q     = card.question;
  const isOX  = card.type === 'choice_ox';

  // Correctness
  const correct = isOX
    ? (S.chosen === 'O') === card.choice.is_correct
    : S.chosen === 'O'; // MCQ self-assess: O = "I knew it"

  // Warning badge
  let warningBadge = '';
  if (q.is_outdated) {
    warningBadge = `<div class="warning-badge warning-outdated">⚠️ 출제 당시 법령 — 현행 법령과 다를 수 있습니다</div>`;
  } else if (q.needs_revision) {
    warningBadge = `<div class="warning-badge warning-revision">✏️ 개정 검토 필요 문항입니다</div>`;
  }

  // Answer section
  let answerSection;
  if (isOX) {
    answerSection = `
      <div class="ox-result">
        <div class="ox-statement-text">${fmt(card.choice.content)}</div>
        <div class="ox-actual ${card.choice.is_correct ? 'ox-correct' : 'ox-wrong'}">
          <span class="ox-verdict">${card.choice.is_correct ? 'O (맞음)' : 'X (틀림)'}</span>
          <span class="choice-mark">${correct ? '✓' : '✗'}</span>
        </div>
      </div>
    `;
  } else {
    const { answer } = S.revealData;
    const choices = [...(q.choices || [])].sort((a, b) => a.choice_number - b.choice_number);
    answerSection = `
      <div class="choices">
        ${choices.map(c => {
          const n = c.choice_number;
          const isAns = n === answer;
          return `
            <div class="choice revealed${isAns ? ' choice-correct' : ''}">
              <span class="choice-num">${esc(n)}</span>
              <span class="choice-text">${esc(c.content)}</span>
              ${isAns ? '<span class="choice-mark">✓</span>' : ''}
            </div>
          `;
        }).join('')}
      </div>
    `;
  }

  // ── Feature 1: Textbook-Style Explanation (UNION OX Format) ─────────────
  const ch = isOX ? card.choice : null;

  // Detect if this card has the new structured explanation fields
  const hasTextbook = isOX && ch && (ch.explanation_core || ch.explanation);

  let explanationCoreHtml = '';
  let fullExplanationHtml = '';

  if (hasTextbook) {
    // New textbook block: □ badge + core reasoning + ①②③ detail + citation
    const isCorrect   = ch.is_correct;
    const conclusion  = isCorrect ? 'O' : 'X';
    const badgeClass  = isCorrect ? 'badge-o' : 'badge-x';
    const coreReason  = ch.explanation_core || '';   // one-sentence principle
    const detailText  = ch.explanation || '';        // step-by-step ①②③
    // Build citation from legal_basis + case_citation if not a single field
    const citParts    = [ch.legal_basis, ch.case_citation].filter(Boolean);
    const citStr      = citParts.length ? '(' + citParts.join('; ') + ')' : '';

    explanationCoreHtml = `
      <div class="textbook-explanation">
        <div class="textbook-conclusion">
          <span class="conclusion-badge ${badgeClass}">${esc(conclusion)}</span>
          ${coreReason ? `<span class="core-reasoning">${fmtCite(coreReason)}</span>` : ''}
        </div>
        ${detailText ? `<div class="textbook-detail">${fmtCite(detailText)}</div>` : ''}
        ${citStr ? `<div class="textbook-citation">${linkCitations(esc(citStr))}</div>` : ''}
      </div>
    `;
    // No separate fullExplanationHtml when textbook format is used
  } else {
    // Legacy: show explanation_core as "핵심 해설" chip
    if (isOX && ch && ch.explanation_core) {
      explanationCoreHtml = `
        <div class="explanation">
          <div class="explanation-title">핵심 해설</div>
          <div class="explanation-text">${fmtCite(ch.explanation_core)}</div>
        </div>
      `;
    }
    // Full Explanation (legacy)
    const explanation = isOX ? q.explanation : S.revealData.explanation;
    fullExplanationHtml = `
      <div class="explanation">
        <div class="explanation-title">상세 해설</div>
        <div class="explanation-text">${
          explanation
            ? fmtCite(explanation)
            : '<span class="explanation-placeholder">해설이 아직 제공되지 않은 문항입니다.</span>'
        }</div>
      </div>
    `;
  }

  // Rich metadata — Union Textbook Style: blue box for statute, purple box for precedent
  const oxRaw = card._ox;
  const richMeta = (() => {
    const importance   = (oxRaw && oxRaw.importance)    || null;
    const legalBasis   = (oxRaw && oxRaw.legal_basis)   || (isOX && card.choice && card.choice.legal_basis)   || null;
    const caseCitation = (oxRaw && oxRaw.case_citation) || (isOX && card.choice && card.choice.case_citation) || null;
    const theory       = oxRaw ? oxRaw.theory       : null;
    const isRevised    = oxRaw ? oxRaw.is_revised   : false;
    const revisionNote = oxRaw ? oxRaw.revision_note : null;
    const impMap = { A: '🔴 핵심 (A)', B: '🟡 표준 (B)', C: '⚪ 주변 (C)' };
    let html = '';
    if (importance)   html += `<div class="importance-badge importance-${importance}">${impMap[importance] || importance}</div>`;
    if (legalBasis)   html += `<div class="legal-basis-box"><span class="legal-box-label">📖 법령 근거</span><div class="legal-box-text">${fmtCite(legalBasis)}</div></div>`;
    if (caseCitation) html += `<div class="case-citation-box"><span class="legal-box-label">⚖️ 판례</span><div class="legal-box-text">${fmtCite(caseCitation)}</div></div>`;
    if (theory)       html += `<div class="theory-row">💡 ${esc(theory)}</div>`;
    if (isRevised && revisionNote) html += `<div class="ox-revision-note">⚠️ 개정: ${esc(revisionNote)}</div>`;
    return html ? `<div class="legal-meta-section">${html}</div>` : '';
  })();

  // Keywords (for OX cards)
  const keywordsHtml = (isOX && card.choice.keywords && card.choice.keywords.length > 0) ? `
    <div class="keywords-section">
      <div class="keywords-title">키워드</div>
      <div class="keywords-list">
        ${card.choice.keywords.map(k => `<span class="keyword-chip">${esc(k)}</span>`).join('')}
      </div>
    </div>
  ` : '';

  // Tags
  const tagsHtml = (q.tags && q.tags.length > 0) ? `
    <div class="tags-row">
      ${q.tags.map(t => `<span class="tag-chip">${esc(t)}</span>`).join('')}
    </div>
  ` : '';

  // Peer stats — wrapper always present so background fetch can update it
  const ps = S.peerStats;
  const peerHtml = `<div id="peer-stats-section">${
    ps && ps.total_attempts > 0 ? `
    <div class="peer-stats">
      <span class="peer-icon">👥</span>
      전체 정답률 <strong>${ps.difficulty_pct}%</strong>
      <span class="peer-total">(${ps.total_attempts.toLocaleString()}명 응답)</span>
    </div>
  ` : ''
  }</div>`;

  // Personal note
  const noteHtml = `
    <div class="note-section">
      <div class="note-label">📝 나의 메모 (두문자/암기법)</div>
      <textarea class="note-area" id="note-area" placeholder="메모를 입력하세요…" rows="3">${esc(card.personal_note || '')}</textarea>
      <button class="btn-save-note" id="btn-save-note">저장</button>
    </div>
  `;

  // Rating buttons with live SM-2 interval preview
  const sm2  = card.sm2;
  const user = S.user;
  const ratingBtns = `
    <button class="btn-rating btn-again" data-rating="1">다시<small>${fmtInterval(calcNextInterval(sm2, 1, user))}</small></button>
    <button class="btn-rating btn-hard"  data-rating="3">어려움<small>${fmtInterval(calcNextInterval(sm2, 3, user))}</small></button>
    <button class="btn-rating btn-good"  data-rating="4">보통<small>${fmtInterval(calcNextInterval(sm2, 4, user))}</small></button>
    <button class="btn-rating btn-easy"  data-rating="5">쉬움<small>${fmtInterval(calcNextInterval(sm2, 5, user))}</small></button>
  `;

  const dynEl = document.getElementById('dynamic-screen');
  dynEl.innerHTML = `
    <div class="study">
      <div class="study-header">
        <button class="btn-back" id="btn-back">← 홈</button>
        <div class="study-meta">
          ${esc(q.source_name || '')}${q.source_year ? ` ${q.source_year}년` : ''}${q.question_number ? ` · ${q.question_number}번` : ''}
        </div>
        <div style="display:flex;align-items:center;gap:8px;">
          <button class="btn-star${card.is_starred ? ' starred' : ''}" id="btn-star" title="즐겨찾기">
            ${card.is_starred ? '★' : '☆'}
          </button>
          <div class="result-badge ${correct ? 'correct' : 'wrong'}">
            ${correct ? '✓ 정답' : '✗ 오답'}
          </div>
        </div>
      </div>

      <div class="question-card">
        ${warningBadge}
        <div class="question-text">${fmt(q.stem)}</div>
        ${answerSection}
        ${richMeta}
        ${explanationCoreHtml}
        ${fullExplanationHtml}
        ${keywordsHtml}
        ${tagsHtml}
        ${peerHtml}
        ${noteHtml}
      </div>

      <div class="rating-bar">
        <span class="rating-label">얼마나 알았나요?</span>
        <div class="rating-btns">${ratingBtns}</div>
      </div>
    </div>
    <button class="ai-fab" id="ai-fab-btn" title="AI 튜터에게 질문하기">🤖</button>
  `;

  document.getElementById('btn-back').addEventListener('click',
    S.returnTo === 'mypage' ? () => showMyPage('history') : showHome);
  document.getElementById('btn-star').addEventListener('click', () => toggleStar(q.id, card.is_starred));
  document.getElementById('btn-save-note').addEventListener('click', () => saveNote(q.id));
  document.querySelectorAll('.btn-rating').forEach(btn => {
    btn.addEventListener('click', () => submitRating(parseInt(btn.dataset.rating)));
  });
  document.getElementById('ai-fab-btn')?.addEventListener('click', () => openAITutor(card));
}

// ── AI Tutor ──────────────────────────────────────────────────────────────────
let _aiChatHistory = [];

function openAITutor(card) {
  _aiChatHistory = [];
  document.getElementById('ai-tutor-overlay')?.remove();

  const overlay = document.createElement('div');
  overlay.id = 'ai-tutor-overlay';
  overlay.className = 'ai-tutor-modal';

  const stem = card.question.stem || '';
  const stmt = card.choice ? card.choice.content : '';
  const context = `문제: ${stem}\n선택지: ${stmt}`;

  overlay.innerHTML = `
    <div class="ai-tutor-panel">
      <div class="ai-tutor-header">
        <span class="ai-tutor-title">🤖 AI 튜터 — 법률 질의응답</span>
        <button class="ai-tutor-close" id="ai-tutor-close">✕</button>
      </div>
      <div class="ai-chat-history" id="ai-chat-history">
        <div class="ai-msg assistant">
          <div class="ai-msg-bubble">안녕하세요! 이 문제에 대해 궁금한 점을 질문해 주세요. 법령 근거나 판례도 설명해 드릴게요.</div>
        </div>
      </div>
      <div class="ai-tutor-input-row">
        <input type="text" class="ai-tutor-input" id="ai-tutor-input"
          placeholder="예: 이 판례의 핵심 법리는 무엇인가요?" />
        <button class="ai-tutor-send" id="ai-tutor-send">전송</button>
      </div>
    </div>
  `;
  document.getElementById('app').appendChild(overlay);

  overlay.addEventListener('click', e => { if (e.target === overlay) closeAITutor(); });
  document.getElementById('ai-tutor-close').addEventListener('click', closeAITutor);

  const sendBtn = document.getElementById('ai-tutor-send');
  const input   = document.getElementById('ai-tutor-input');
  sendBtn.addEventListener('click', () => sendAITutorMessage(context, card));
  input.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendAITutorMessage(context, card); } });
  input.focus();
}

function closeAITutor() {
  document.getElementById('ai-tutor-overlay')?.remove();
}

async function sendAITutorMessage(context, card) {
  const input   = document.getElementById('ai-tutor-input');
  const sendBtn = document.getElementById('ai-tutor-send');
  const history = document.getElementById('ai-chat-history');
  const msg = input.value.trim();
  if (!msg) return;

  input.value = '';
  sendBtn.disabled = true;

  const userBubble = document.createElement('div');
  userBubble.className = 'ai-msg user';
  userBubble.innerHTML = `<div class="ai-msg-bubble">${esc(msg)}</div>`;
  history.appendChild(userBubble);
  history.scrollTop = history.scrollHeight;

  const thinkBubble = document.createElement('div');
  thinkBubble.className = 'ai-msg assistant';
  thinkBubble.id = 'ai-thinking';
  thinkBubble.innerHTML = `<div class="ai-msg-bubble" style="opacity:.6">⏳ 분석 중…</div>`;
  history.appendChild(thinkBubble);
  history.scrollTop = history.scrollHeight;

  _aiChatHistory.push({ role: 'user', content: msg });

  try {
    const resp = await api.post('/chat/explain', {
      card_id: card.flashcard_id || '',
      message: msg,
      context,
      history: _aiChatHistory.slice(-6),
    });
    const reply = resp.response || '응답을 받지 못했습니다.';
    _aiChatHistory.push({ role: 'assistant', content: reply });
    document.getElementById('ai-thinking')?.remove();
    const aiBubble = document.createElement('div');
    aiBubble.className = 'ai-msg assistant';
    aiBubble.innerHTML = `<div class="ai-msg-bubble">${fmt(reply)}</div>`;
    history.appendChild(aiBubble);
    history.scrollTop = history.scrollHeight;
  } catch (err) {
    document.getElementById('ai-thinking')?.remove();
    const errBubble = document.createElement('div');
    errBubble.className = 'ai-msg assistant';
    errBubble.innerHTML = `<div class="ai-msg-bubble" style="color:var(--danger)">오류: ${esc(err.message)}</div>`;
    history.appendChild(errBubble);
  } finally {
    sendBtn.disabled = false;
    input.focus();
  }
}

// ── Star toggle ───────────────────────────────────────────────────────────────
async function toggleStar(questionId, currentlyStarred) {
  const newVal = !currentlyStarred;
  try {
    await api.put(`/questions/${questionId}/star`, { is_starred: newVal });
    S.card.is_starred = newVal;
    const btn = document.getElementById('btn-star');
    if (btn) {
      btn.textContent = newVal ? '★' : '☆';
      btn.classList.toggle('starred', newVal);
    }
  } catch (err) {
    console.error('Star toggle failed:', err);
  }
}

// ── Personal note save ────────────────────────────────────────────────────────
async function saveNote(questionId) {
  const textarea = document.getElementById('note-area');
  const btn      = document.getElementById('btn-save-note');
  if (!textarea || !btn) return;

  const note = textarea.value.trim() || null;
  btn.disabled = true;
  btn.textContent = '저장 중…';
  try {
    await api.put(`/questions/${questionId}/note`, { personal_note: note });
    S.card.personal_note = note;
    btn.textContent = '✓ 저장됨';
    setTimeout(() => { btn.textContent = '저장'; btn.disabled = false; }, 1500);
  } catch (err) {
    console.error('Note save failed:', err);
    btn.textContent = '저장';
    btn.disabled = false;
  }
}

async function submitRating(rating) {
  document.querySelectorAll('.btn-rating').forEach(b => (b.disabled = true));

  const flashcardId = S.card.flashcard_id;
  const isOX = S.card.type === 'choice_ox';

  // FIX M-5: include time spent (ms since card was first shown)
  const timeSpentMs = S.cardShownAt ? (Date.now() - S.cardShownAt) : undefined;
  S.cardShownAt = null;

  // answer_given required for MCQ (C-2 fix on backend; also send from frontend)
  const body = { rating, time_spent_ms: timeSpentMs };
  if (!isOX && typeof S.chosen === 'number') {
    body.answer_given = S.chosen;
  }

  let submitOk = true;
  if (S.isMockMode) {
    // Mock mode: no DB review, just advance
    S.sessionDone++;
  } else {
    try {
      await api.post(`/reviews/${flashcardId}`, body);
    } catch (err) {
      console.error(err);
      submitOk = false;
    }
    if (submitOk) {
      S.sessionDone++;
      // FIX M-3: set up undo buffer — 8-second window to undo last rating
      clearUndoBuffer();
      const undoTimer = setTimeout(() => { S.undoBuffer = null; }, 8000);
      S.undoBuffer = { flashcardId, timer: undoTimer };
      showUndoToast(flashcardId);
    }
  }

  if (S.returnTo === 'mypage') {
    S.returnTo = null;
    await showMyPage('history');
  } else {
    await fetchNextCard();
  }
}

// ── Undo support ──────────────────────────────────────────────────────────────
function clearUndoBuffer() {
  if (S.undoBuffer) {
    clearTimeout(S.undoBuffer.timer);
    S.undoBuffer = null;
  }
  const toast = document.getElementById('undo-toast');
  if (toast) toast.remove();
}

function showUndoToast(flashcardId) {
  // Remove existing toast if any
  document.getElementById('undo-toast')?.remove();

  const toast = document.createElement('div');
  toast.id = 'undo-toast';
  toast.className = 'undo-toast';
  toast.innerHTML = `평가 완료 &nbsp;<button class="undo-toast-btn" id="undo-btn">↩ 실수 취소</button>`;
  document.getElementById('app').appendChild(toast);

  document.getElementById('undo-btn').addEventListener('click', async () => {
    clearUndoBuffer();
    try {
      await api.request('DELETE', `/reviews/${flashcardId}/undo-last`);
      // Re-show the card that was just rated
      const card = await api.get(`/flashcards/${flashcardId}`);
      S.card       = card;
      S.chosen     = null;
      S.revealData = null;
      S.peerStats  = null;
      S.sessionDone = Math.max(0, S.sessionDone - 1);
      S.screen     = 'study';
      renderStudy();
    } catch (e) {
      console.error('Undo failed:', e);
    }
  });

  // Auto-dismiss after 8s
  setTimeout(() => toast?.remove(), 8000);
}

// ── DONE ──────────────────────────────────────────────────────────────────────
function renderDone() {
  showBottomNav();
  setActiveTab('home');
  const dynEl = document.getElementById('dynamic-screen');
  dynEl.innerHTML = `
    <div class="done-screen">
      <div class="done-icon">🎉</div>
      <h2>오늘 학습 완료!</h2>
      <p>이 세션에서 <strong>${S.sessionDone}개</strong>의 카드를 복습했습니다.</p>
      <button class="btn-home" id="btn-home">홈으로</button>
    </div>
  `;
  document.getElementById('btn-home').addEventListener('click', showHome);
}

// ── Help modal ────────────────────────────────────────────────────────────────
function showHelpModal() {
  document.getElementById('help-modal').hidden = false;
}
function hideHelpModal() {
  document.getElementById('help-modal').hidden = true;
}

// ── Keyboard shortcuts ────────────────────────────────────────────────────────
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

  // FIX L-5: ? key toggles help modal from anywhere
  if (e.key === '?') {
    const modal = document.getElementById('help-modal');
    modal.hidden ? showHelpModal() : hideHelpModal();
    return;
  }

  // FIX M-3: U key triggers undo from anywhere
  if ((e.key === 'u' || e.key === 'U') && S.undoBuffer) {
    document.getElementById('undo-btn')?.click();
    return;
  }

  if (S.screen === 'study') {
    if (e.key === 'o' || e.key === 'O') selectOX('O');
    else if (e.key === 'x' || e.key === 'X') selectOX('X');

  } else if (S.screen === 'result') {
    if (e.key === 'a' || e.key === 'A' || e.key === 'Enter') submitRating(1);
    else if (e.key === 'h' || e.key === 'H') submitRating(3);
    else if (e.key === 'g' || e.key === 'G' || e.key === ' ') { e.preventDefault(); submitRating(4); }
    else if (e.key === 'e' || e.key === 'E') submitRating(5);

  } else if (S.screen === 'home') {
    if (e.key === 'Enter') document.getElementById('btn-cta')?.click();

  } else if (S.screen === 'quickscan') {
    if (e.key === ' ' || e.key === 'ArrowRight' || e.key === 'Enter') {
      e.preventDefault();
      document.getElementById('qs-card')?.click();
    }
  }
});

// ── My Page ───────────────────────────────────────────────────────────────────
async function showMyPage(tab) {
  if (tab !== undefined) S.myPageTab = tab;
  S.screen = 'mypage';
  showBottomNav();
  setActiveTab('mypage');
  showLoading();
  document.getElementById('login-screen').hidden = true;
    
  let data = {};
  try {
    if (S.myPageTab === 'stats') {
      const [stats, subjStats, weekly] = await Promise.all([
        api.get('/stats/'),
        api.get('/stats/subjects'),
        api.get('/stats/weekly').catch(() => null),
      ]);
      S.stats        = stats;
      S.subjectStats = subjStats;
      S.streak       = stats.study_streak;
      S.weeklyStats  = weekly;
      data = { stats, subjStats, weekly };
    } else if (S.myPageTab === 'history') {
      // FIX M-10: reset offset when switching to history tab
      S.historyOffset = 0;
      S.wrongOnlyFilter = false;
      S.historySubjectId = null;
      const PAGE = 30;
      const logs = await api.get(`/reviews/history?limit=${PAGE + 1}&offset=0`);
      S.historyHasMore = logs.length > PAGE;
      data.logs = logs.slice(0, PAGE);
      S.historyOffset = PAGE;
    } else if (S.myPageTab === 'bookmarks') {
      data.starred_questions = await api.get('/questions/starred');
    } else if (S.myPageTab === 'settings') {
      const user = await api.get('/users/me');
      S.user = user;
      data.user = user;
    }
  } catch(e) {
    console.error(e);
  }
  hideLoading();
  renderMyPage(data);
}

function renderMyPage(data) {
  const tab = S.myPageTab;
  const tabDefs = [
    { id: 'stats',     label: '내 통계' },
    { id: 'history',   label: '오답노트' },
    { id: 'bookmarks', label: '북마크' },
    { id: 'settings',  label: '설정' },
  ];
  const tabHtml = tabDefs.map(t => `
    <button class="mypage-tab-btn ${tab === t.id ? 'active' : ''}" data-tab="${t.id}">${t.label}</button>
  `).join('');

  let contentHtml = '';

  if (tab === 'stats') {
    const { stats, weekly } = data;
    if (!stats) {
      contentHtml = '<div class="review-empty">통계를 불러올 수 없습니다.</div>';
    } else {
      // Weekly chart
      const days = (weekly && weekly.days) || [];
      const maxReviewed = Math.max(...days.map(d => d.reviewed), 1);
      const DOW = ['일','월','화','수','목','금','토'];
      const weeklyChartHtml = days.length > 0 ? `
        <div class="weekly-chart-card">
          <div class="weekly-chart-title">📈 7일 학습 현황</div>
          <div class="weekly-chart">
            ${days.map(d => {
              const date = new Date(d.date + 'T00:00:00');
              const dow  = DOW[date.getDay()];
              const pct  = Math.round((d.reviewed / maxReviewed) * 100);
              const accPct = d.accuracy;
              return `
                <div class="weekly-bar-col">
                  <div class="weekly-bar-wrap">
                    <div class="weekly-bar-fill" style="height:${pct}%"
                         title="${d.reviewed}개 학습, 정확도 ${accPct}%"></div>
                  </div>
                  <div class="weekly-bar-label">${dow}</div>
                  <div class="weekly-bar-val">${d.reviewed > 0 ? d.reviewed : ''}</div>
                </div>
              `;
            }).join('')}
          </div>
        </div>
      ` : '';

      contentHtml = `
        ${weeklyChartHtml}
        <div class="stat-big-card">
          <div class="stat-big-label">7일 정확도</div>
          <div class="stat-big-value">${stats.accuracy_7d.toFixed(1)}%</div>
          <div class="stat-big-sub">최근 7일간 학습 정확도</div>
        </div>
        <div class="stats-2col">
          <div class="stat-big-card" style="margin:0">
            <div class="stat-big-label">전체 카드</div>
            <div class="stat-big-value" style="font-size:1.6rem">${stats.total_cards.toLocaleString()}</div>
          </div>
          <div class="stat-big-card" style="margin:0">
            <div class="stat-big-label">연속 학습</div>
            <div class="stat-big-value" style="font-size:1.6rem">${S.streak > 0 ? `🔥 ${S.streak}일` : '-'}</div>
          </div>
        </div>
        <div class="stat-big-card">
          <div class="stat-big-label">오늘</div>
          <div style="display:flex;gap:28px;margin-top:8px;">
            <div>
              <div style="font-size:1.5rem;font-weight:800;color:var(--warning)">${stats.reviewed_today}</div>
              <div style="font-size:.72rem;color:var(--text-muted)">완료</div>
            </div>
            <div>
              <div style="font-size:1.5rem;font-weight:800;color:var(--success)">${stats.correct_today}</div>
              <div style="font-size:.72rem;color:var(--text-muted)">정답</div>
            </div>
            <div>
              <div style="font-size:1.5rem;font-weight:800;color:var(--primary)">${stats.due_today}</div>
              <div style="font-size:.72rem;color:var(--text-muted)">예정</div>
            </div>
          </div>
        </div>
      `;
    }
  } else if (tab === 'history') {
    const logs = data.logs || [];
    function relTime(iso) {
      const min = Math.floor((Date.now() - new Date(iso)) / 60000);
      if (min < 1) return '방금';
      if (min < 60) return `${min}분 전`;
      const hr = Math.floor(min / 60);
      if (hr < 24) return `${hr}시간 전`;
      const d = Math.floor(hr / 24);
      return d < 7 ? `${d}일 전` : `${Math.floor(d / 7)}주 전`;
    }
    const subjectOpts = (S.subjects || []).map(s =>
      `<option value="${esc(s.id)}" ${S.historySubjectId === s.id ? 'selected' : ''}>${esc(s.name)}</option>`
    ).join('');
    const filterBar = `
      <div class="history-filter-bar">
        <div class="history-filter-btns">
          <button class="hf-btn ${!S.wrongOnlyFilter ? 'active' : ''}" id="hf-all">전체</button>
          <button class="hf-btn ${S.wrongOnlyFilter ? 'active' : ''}" id="hf-wrong">❌ 오답만</button>
        </div>
        <select id="hf-subject" class="hf-subject-select">
          <option value="">전체 과목</option>
          ${subjectOpts}
        </select>
      </div>
    `;
    const itemsHtml = logs.length === 0
      ? '<div class="review-empty">해당하는 학습 기록이 없어요</div>'
      : logs.map(log => {
          const stem  = (log.question_stem || '').slice(0, 60) +
                        ((log.question_stem || '').length > 60 ? '…' : '');
          const badge = log.card_type === 'choice_ox'
            ? '<span class="review-badge review-badge-ox">O/X</span>'
            : '<span class="review-badge review-badge-mcq">MCQ</span>';
          const dots  = '●'.repeat(Math.min(log.rating, 5)) + '○'.repeat(5 - Math.min(log.rating, 5));
          const subjectTag = log.subject_name
            ? `<span class="review-subject-tag">${esc(log.subject_name)}</span>` : '';
          return `<div class="review-item" data-flashcard-id="${esc(log.flashcard_id)}">
            <div class="review-item-top">${badge}${subjectTag}</div>
            <div class="review-stem">${esc(stem || '(문제 정보 없음)')}</div>
            <div class="review-meta">
              <span class="review-correct ${log.was_correct ? 'correct' : 'wrong'}">${log.was_correct ? '✓' : '✗'}</span>
              <span class="review-rating">${dots}</span>
              <span class="review-time">${relTime(log.reviewed_at)}</span>
              <button class="btn-restudy" data-flashcard-id="${esc(log.flashcard_id)}">▶ 다시</button>
            </div></div>`;
        }).join('');
    const loadMoreHtml = S.historyHasMore
      ? `<button class="btn-load-more" id="btn-load-more">더 보기</button>`
      : '';
    contentHtml = `${filterBar}<div class="review-list" id="review-list-container">${itemsHtml}</div>${loadMoreHtml}`;
  } else if (tab === 'bookmarks') {
    const questions = data.starred_questions || [];
    const itemsHtml = questions.length === 0
      ? '<div class="review-empty">북마크한 문제가 없습니다.</div>'
      : questions.map(q => {
          const stem  = (q.stem || '').slice(0, 60) +
                        ((q.stem || '').length > 60 ? '…' : '');
          return `<div class="review-item" data-flashcard-id="${esc(q.flashcard_id)}">
            <div class="review-stem">${esc(stem || '(문제 정보 없음)')}</div>
            <div class="review-meta">
              <span class="review-subject">${esc(q.subject_name)}</span>
              <button class="btn-restudy" data-flashcard-id="${esc(q.flashcard_id)}">▶ 다시</button>
            </div></div>`;
        }).join('');
    contentHtml = `<div class="review-list" id="review-list-container">${itemsHtml}</div>`;
  } else if (tab === 'settings') {
    const user       = data.user || {};
    const isDark     = document.body.classList.contains('dark-mode');
    const isVacation = !!(user.vacation_mode_enabled);
    const retPct     = user.target_retention != null ? Math.round(user.target_retention * 100) : 90;
    contentHtml = `
      <div class="settings-section">
        <div class="settings-item">
          <span class="settings-item-label">☀️ 다크 모드</span>
          <label class="toggle-switch">
            <input type="checkbox" id="toggle-dark" ${isDark ? 'checked' : ''}>
            <div class="toggle-track"></div>
          </label>
        </div>
        <div class="settings-item">
          <span class="settings-item-label">🏖️ 휴가 모드</span>
          <label class="toggle-switch">
            <input type="checkbox" id="toggle-vacation" ${isVacation ? 'checked' : ''}>
            <div class="toggle-track"></div>
          </label>
        </div>
      </div>

      <div class="settings-section">
        <div class="srs-settings-title">⚙️ SRS 학습 설정</div>
        <div class="srs-row">
          <label class="srs-label">일일 신규 카드 한도</label>
          <div class="srs-input-row">
            <input type="range" id="srs-new-limit" min="0" max="100" step="1"
              value="${user.daily_new_limit ?? 20}" class="srs-slider">
            <span class="srs-slider-val" id="srs-new-limit-val">${user.daily_new_limit ?? 20}장</span>
          </div>
        </div>
        <div class="srs-row">
          <label class="srs-label">일일 복습 한도</label>
          <div class="srs-input-row">
            <input type="range" id="srs-rev-limit" min="0" max="500" step="10"
              value="${user.daily_review_limit ?? 200}" class="srs-slider">
            <span class="srs-slider-val" id="srs-rev-limit-val">${user.daily_review_limit ?? 200}장</span>
          </div>
        </div>
        <div class="srs-row">
          <label class="srs-label">목표 기억률</label>
          <div class="srs-input-row">
            <input type="range" id="srs-retention" min="50" max="99" step="1"
              value="${retPct}" class="srs-slider">
            <span class="srs-slider-val" id="srs-retention-val">${retPct}%</span>
          </div>
        </div>
        <div class="srs-row">
          <label class="srs-label">학습 단계 (분, 공백 구분)</label>
          <input type="text" id="srs-steps" class="srs-text-input"
            value="${esc(user.learning_steps ?? '1 10')}" placeholder="예: 1 10">
        </div>
        <div class="srs-row">
          <label class="srs-label">재학습 단계 (분, 공백 구분)</label>
          <input type="text" id="srs-resteps" class="srs-text-input"
            value="${esc(user.relearning_steps ?? '10')}" placeholder="예: 10">
        </div>
        <button class="srs-save-btn" id="srs-save-btn">저장</button>
        <div class="srs-save-msg" id="srs-save-msg"></div>
      </div>

      ${user.display_name || user.email ? `
        <div class="settings-section">
          <div class="settings-item" style="cursor:default">
            <span class="settings-item-label">계정</span>
            <span class="settings-item-right">${esc(user.display_name || user.email || '')}</span>
          </div>
        </div>
      ` : ''}
      <div class="settings-section">
        <div class="settings-item danger" id="settings-logout">
          <span class="settings-item-label">로그아웃</span>
          <span class="settings-item-right">→</span>
        </div>
      </div>
    `;
  }

  const dynEl = document.getElementById('dynamic-screen');
  dynEl.innerHTML = `
    <div class="mypage-v2">
      <div class="home-topbar" style="margin-bottom:16px">
        <span class="home-topbar-title">👤 내 정보</span>
      </div>
      <div class="mypage-tabs">${tabHtml}</div>
      <div class="mypage-content">${contentHtml}</div>
    </div>
  `;

  document.querySelectorAll('.mypage-tab-btn').forEach(btn => {
    btn.addEventListener('click', () => showMyPage(btn.dataset.tab));
  });

  // 오답노트 필터 버튼
  document.getElementById('hf-all')?.addEventListener('click', async () => {
    S.wrongOnlyFilter = false;
    await _reloadHistory();
  });
  document.getElementById('hf-wrong')?.addEventListener('click', async () => {
    S.wrongOnlyFilter = true;
    await _reloadHistory();
  });
  document.getElementById('hf-subject')?.addEventListener('change', async e => {
    S.historySubjectId = e.target.value || null;
    await _reloadHistory();
  });

  // 오답노트 "다시 풀기" buttons
  document.getElementById('review-list-container')?.addEventListener('click', e => {
    const btn = e.target.closest('.btn-restudy');
    if (btn) studySpecificCard(btn.dataset.flashcardId);
  });

  // FIX M-10: Load more history
  document.getElementById('btn-load-more')?.addEventListener('click', async () => {
    const loadMoreBtn = document.getElementById('btn-load-more');
    if (loadMoreBtn) { loadMoreBtn.disabled = true; loadMoreBtn.textContent = '로딩 중…'; }
    const PAGE = 30;
    try {
      const moreLogs = await api.get(`/reviews/history?limit=${PAGE + 1}&offset=${S.historyOffset}`);
      const hasMore = moreLogs.length > PAGE;
      const newLogs = moreLogs.slice(0, PAGE);
      S.historyOffset += PAGE;
      S.historyHasMore = hasMore;

      const container = document.getElementById('review-list-container');
      if (container) {
        function relTime(iso) {
          const min = Math.floor((Date.now() - new Date(iso)) / 60000);
          if (min < 1) return '방금';
          if (min < 60) return `${min}분 전`;
          const hr = Math.floor(min / 60);
          if (hr < 24) return `${hr}시간 전`;
          const d = Math.floor(hr / 24);
          return d < 7 ? `${d}일 전` : `${Math.floor(d / 7)}주 전`;
        }
        const newHtml = newLogs.map(log => {
          const stem  = (log.question_stem || '').slice(0, 60) + ((log.question_stem || '').length > 60 ? '…' : '');
          const badge = log.card_type === 'choice_ox'
            ? '<span class="review-badge review-badge-ox">O/X</span>'
            : '<span class="review-badge review-badge-mcq">MCQ</span>';
          const dots  = '●'.repeat(Math.min(log.rating, 5)) + '○'.repeat(5 - Math.min(log.rating, 5));
          return `<div class="review-item" data-flashcard-id="${esc(log.flashcard_id)}">
            ${badge}
            <div class="review-stem">${esc(stem || '(문제 정보 없음)')}</div>
            <div class="review-meta">
              <span class="review-correct ${log.was_correct ? 'correct' : 'wrong'}">${log.was_correct ? '✓' : '✗'}</span>
              <span class="review-rating">${dots}</span>
              <span class="review-time">${relTime(log.reviewed_at)}</span>
              <button class="btn-restudy" data-flashcard-id="${esc(log.flashcard_id)}">▶ 다시</button>
            </div></div>`;
        }).join('');
        container.insertAdjacentHTML('beforeend', newHtml);
        container.addEventListener('click', e => {
          const btn = e.target.closest('.btn-restudy');
          if (btn) studySpecificCard(btn.dataset.flashcardId);
        });
      }

      if (hasMore) {
        if (loadMoreBtn) { loadMoreBtn.disabled = false; loadMoreBtn.textContent = '더 보기'; }
      } else {
        loadMoreBtn?.remove();
      }
    } catch(e) {
      console.error(e);
      if (loadMoreBtn) { loadMoreBtn.disabled = false; loadMoreBtn.textContent = '더 보기'; }
    }
  });

  // 북마크 "다시 풀기" buttons
  document.getElementById('review-list-container')?.addEventListener('click', e => {
    const btn = e.target.closest('.btn-restudy');
    if (btn) studySpecificCard(btn.dataset.flashcardId);
  });

  if (tab === 'settings') {
    document.getElementById('toggle-dark')?.addEventListener('change', toggleDarkMode);
    document.getElementById('settings-logout')?.addEventListener('click', logout);

    const vacToggle = document.getElementById('toggle-vacation');
    if (vacToggle) {
      vacToggle.addEventListener('change', async () => {
        try {
          await api.put('/users/me/vacation', { enabled: vacToggle.checked });
          if (S.user) S.user.vacation_mode_enabled = vacToggle.checked;
        } catch(e) {
          console.error(e);
          vacToggle.checked = !vacToggle.checked;
        }
      });
    }

    // ── SRS slider live labels ────────────────────────────────────────────────
    const newSlider = document.getElementById('srs-new-limit');
    const revSlider = document.getElementById('srs-rev-limit');
    const retSlider = document.getElementById('srs-retention');
    newSlider?.addEventListener('input', () => {
      document.getElementById('srs-new-limit-val').textContent = newSlider.value + '장';
    });
    revSlider?.addEventListener('input', () => {
      document.getElementById('srs-rev-limit-val').textContent = revSlider.value + '장';
    });
    retSlider?.addEventListener('input', () => {
      document.getElementById('srs-retention-val').textContent = retSlider.value + '%';
    });

    // ── SRS save ──────────────────────────────────────────────────────────────
    document.getElementById('srs-save-btn')?.addEventListener('click', async () => {
      const btn = document.getElementById('srs-save-btn');
      const msg = document.getElementById('srs-save-msg');
      btn.disabled = true;
      btn.textContent = '저장 중…';
      try {
        const body = {
          daily_new_limit:    parseInt(newSlider.value),
          daily_review_limit: parseInt(revSlider.value),
          target_retention:   parseInt(retSlider.value) / 100,
          learning_steps:     document.getElementById('srs-steps').value.trim() || '1 10',
          relearning_steps:   document.getElementById('srs-resteps').value.trim() || '10',
        };
        const updated = await api.put('/users/me/study-settings', body);
        S.user = updated;
        msg.textContent = '✓ 저장됨';
        msg.style.color = 'var(--success)';
        setTimeout(() => { msg.textContent = ''; }, 2000);
      } catch(e) {
        console.error(e);
        msg.textContent = '저장 실패';
        msg.style.color = 'var(--danger)';
      } finally {
        btn.disabled = false;
        btn.textContent = '저장';
      }
    });
  }
}

// ── Quick Scan (M-6) ─────────────────────────────────────────────────────────
const QS_MODE_LABELS = { failure: '📉 오답 집중', newest: '🆕 최신 문제', favorites: '⭐ 즐겨찾기' };

async function showQuickScan(mode = 'failure') {
  S.qsMode    = mode;
  S.qsIdx     = 0;
  S.qsFlipped = false;
  S.screen    = 'quickscan';
  hideBottomNav();
  showLoading();
  try {
    S.qsCards = await api.get(`/cards/quick-scan?mode=${mode}&limit=50`);
  } catch(e) {
    console.error(e);
    showBottomNav(); setActiveTab('home');
    hideLoading();
    return;
  }
  hideLoading();
  if (!S.qsCards.length) {
    document.getElementById('dynamic-screen').innerHTML = `
      <div class="quick-scan">
        <div class="quick-scan-header">
          <button class="btn-back" id="qs-exit">← 나가기</button>
          <span>${QS_MODE_LABELS[mode]}</span>
          <span></span>
        </div>
        <div class="quick-scan-empty">카드가 없습니다</div>
      </div>`;
    document.getElementById('qs-exit').addEventListener('click', () => { showBottomNav(); setActiveTab('home'); showHome(); });
    return;
  }
  renderQSCard();
}

function renderQSCard() {
  const card = S.qsCards[S.qsIdx];
  if (!card) {
    // Done
    document.getElementById('dynamic-screen').innerHTML = `
      <div class="quick-scan">
        <div class="quick-scan-done">
          <div class="done-icon">✅</div>
          <h2>빠른 복습 완료!</h2>
          <p>${S.qsCards.length}장을 훑었습니다.</p>
          <button class="btn-home" id="qs-done-home">홈으로</button>
        </div>
      </div>`;
    document.getElementById('qs-done-home').addEventListener('click', () => { showBottomNav(); setActiveTab('home'); showHome(); });
    return;
  }

  const isOX  = card.type === 'choice_ox';
  const q     = card.question;
  const prog  = `${S.qsIdx + 1} / ${S.qsCards.length}`;
  const pct   = Math.round((S.qsIdx / S.qsCards.length) * 100);
  const dynEl = document.getElementById('dynamic-screen');

  if (!S.qsFlipped) {
    // Question side
    const questionBody = isOX
      ? `<div class="ox-statement"><div class="ox-statement-text">${fmt(card.choice.content)}</div></div>`
      : `<div class="question-text">${fmt(q.stem)}</div>`;
    dynEl.innerHTML = `
      <div class="quick-scan">
        <div class="quick-scan-header">
          <button class="btn-back" id="qs-exit">← 나가기</button>
          <span class="quick-scan-mode-badge">${QS_MODE_LABELS[S.qsMode]}</span>
          <span class="quick-scan-counter">${prog}</span>
        </div>
        <div class="qs-progress-bar"><div class="qs-progress-fill" style="width:${pct}%"></div></div>
        <div class="quick-scan-card" id="qs-card" role="button" tabindex="0">
          ${questionBody}
          <div class="qs-flip-hint">탭하여 답 확인 ↓</div>
        </div>
      </div>`;
  } else {
    // Answer side
    const correctAnswer = isOX ? (card.choice.is_correct ? 'O' : 'X') : q.correct_choice;
    const answerBody = isOX ? `
      <div class="qs-answer-badge ${card.choice.is_correct ? 'qs-correct' : 'qs-wrong'}">
        ${card.choice.is_correct ? 'O (맞음)' : 'X (틀림)'}
      </div>` : `
      <div class="qs-answer-badge qs-correct">정답: ${correctAnswer}번</div>
      <div class="question-text" style="font-size:.85rem;opacity:.8">${fmt(q.stem)}</div>`;
    const expHtml = q.explanation
      ? `<div class="qs-explanation">${fmt(q.explanation)}</div>`
      : '';
    dynEl.innerHTML = `
      <div class="quick-scan">
        <div class="quick-scan-header">
          <button class="btn-back" id="qs-exit">← 나가기</button>
          <span class="quick-scan-mode-badge">${QS_MODE_LABELS[S.qsMode]}</span>
          <span class="quick-scan-counter">${prog}</span>
        </div>
        <div class="qs-progress-bar"><div class="qs-progress-fill" style="width:${pct}%"></div></div>
        <div class="quick-scan-card revealed" id="qs-card" role="button" tabindex="0">
          ${answerBody}
          ${expHtml}
          <div class="qs-flip-hint">탭하여 다음 카드 →</div>
        </div>
      </div>`;
  }

  document.getElementById('qs-exit').addEventListener('click', () => {
    showBottomNav(); setActiveTab('home'); showHome();
  });
  document.getElementById('qs-card').addEventListener('click', () => {
    if (!S.qsFlipped) {
      S.qsFlipped = true;
    } else {
      S.qsIdx++;
      S.qsFlipped = false;
    }
    renderQSCard();
  });
}

// ── Mock Test ────────────────────────────────────────────────────────────────
async function startMockTest() {
  // Redirect to setup screen instead of starting directly
  if (S.subjects && S.subjects.length > 0) {
    showMockSetup();
  } else {
    await startMockWithConfig(null, 20, 0);
  }
}

function renderMockTestQuestion() {
  const { cards, index, answers } = S.mockTest;
  const card = cards[index];
  const q = card.question;
  const userAnswer = answers[index];

  const progress = `${index + 1} / ${cards.length}`;

  const timerHtml = S.mockTest.timeLimit > 0
    ? `<span id="mock-timer" class="mock-timer">⏱ --:--</span>` : '';

  let html = `
    <div class="study">
      <div class="study-header">
        <button class="btn-back" onclick="showHome()">← 홈</button>
        <div class="study-meta">모의고사 ${timerHtml}</div>
        <div class="session-count">${progress}</div>
      </div>
      <div class="question-card" style="padding-bottom: 120px;">
        <div class="question-text">${fmt(q.stem)}</div>
        <div class="ox-statement" style="margin-top: 16px;">
          <div class="ox-statement-text">${fmt(card.choice.content)}</div>
        </div>
      </div>
      <div class="rating-bar" style="position: fixed; bottom: 0; left: 0; right: 0;">
        <div class="rating-btns" style="justify-content: center;">
          <button class="btn-ox btn-ox-o ${userAnswer === 'O' ? 'selected' : ''}" style="width: 80px; height: 80px;" onclick="selectMockAnswer('O')">O</button>
          <button class="btn-ox btn-ox-x ${userAnswer === 'X' ? 'selected' : ''}" style="width: 80px; height: 80px;" onclick="selectMockAnswer('X')">X</button>
        </div>
        <div style="display: flex; justify-content: space-between; padding: 10px 20px; align-items: center;">
          <button onclick="navigateMockTest(-1)" class="btn-rating" ${index === 0 ? 'disabled' : ''}>이전</button>
          <button onclick="showMockTestSummary()" class="btn-rating btn-good">채점하기</button>
          <button onclick="navigateMockTest(1)" class="btn-rating" ${index === cards.length - 1 ? 'disabled' : ''}>다음</button>
        </div>
      </div>
    </div>
  `;

  document.getElementById('dynamic-screen').innerHTML = html;
}

function selectMockAnswer(answer) {
  S.mockTest.answers[S.mockTest.index] = answer;
  // Automatically move to the next question
  if (S.mockTest.index < S.mockTest.cards.length - 1) {
    navigateMockTest(1);
  } else {
    renderMockTestQuestion(); // Rerender to show selection on last question
  }
}

function navigateMockTest(direction) {
  const newIndex = S.mockTest.index + direction;
  if (newIndex >= 0 && newIndex < S.mockTest.cards.length) {
    S.mockTest.index = newIndex;
    renderMockTestQuestion();
  }
}

function showMockTestSummary() {
  const { cards, answers } = S.mockTest;
  let correctCount = 0;

  const resultsHtml = cards.map((card, i) => {
    const correctAnswer = card.choice.is_correct ? 'O' : 'X';
    const userAnswer = answers[i];
    const isCorrect = userAnswer === correctAnswer;
    if (isCorrect) correctCount++;

    return `
      <div class="review-item" style="background: ${isCorrect ? 'var(--bg-correct)' : 'var(--bg-wrong)'};">
        <div class="review-stem"><b>Q${i + 1}.</b> ${esc(card.question.stem)}</div>
        <div class="review-stem" style="padding-left: 1.5em;">${esc(card.choice.content)}</div>
        <div class="review-meta">
          <span>제출: ${userAnswer || '미응답'}</span>
          <span>정답: ${correctAnswer}</span>
          <span class="review-correct ${isCorrect ? 'correct' : 'wrong'}">${isCorrect ? '✓' : '✗'}</span>
        </div>
        ${!isCorrect && card.question.explanation ? `<div class="explanation" style="margin-top: 8px; font-size: 0.8em;">${fmt(card.question.explanation)}</div>` : ''}
      </div>
    `;
  }).join('');

  const score = `${correctCount} / ${cards.length}`;
  const scorePercent = cards.length > 0 ? Math.round((correctCount / cards.length) * 100) : 0;

  let html = `
    <div class="mypage-v2">
      <div class="home-topbar" style="margin-bottom:16px">
        <span class="home-topbar-title">📝 모의고사 결과</span>
      </div>
      <div class="mypage-content">
        <div class="stat-big-card">
          <div class="stat-big-label">총점</div>
          <div class="stat-big-value">${scorePercent}점</div>
          <div class="stat-big-sub">${score}</div>
        </div>
        <div class="review-list">${resultsHtml}</div>
        <button class="btn-cta" style="margin-top: 24px;" onclick="showHome()">홈으로 돌아가기</button>
      </div>
    </div>
  `;

  document.getElementById('dynamic-screen').innerHTML = html;
  S.screen = 'home';
  showBottomNav();
  setActiveTab('home');
}

// ── History reload helper ─────────────────────────────────────────────────────
async function _reloadHistory() {
  S.historyOffset = 0;
  const PAGE = 30;
  const params = new URLSearchParams({ limit: PAGE + 1, offset: 0 });
  if (S.wrongOnlyFilter) params.set('wrong_only', 'true');
  if (S.historySubjectId) params.set('subject_id', S.historySubjectId);
  try {
    showLoading();
    const logs = await api.get(`/reviews/history?${params}`);
    S.historyHasMore = logs.length > PAGE;
    hideLoading();
    const data = { logs: logs.slice(0, PAGE) };
    S.historyOffset = PAGE;
    // Re-render only the content area
    renderMyPage(data);
  } catch(e) {
    console.error(e);
    hideLoading();
  }
}


// ── Search ────────────────────────────────────────────────────────────────────
function showSearch() {
  S.screen = 'search';
  showBottomNav();
  setActiveTab('search');
  document.getElementById('login-screen').hidden = true;
  renderSearch();
}

function renderSearch() {
  const dynEl = document.getElementById('dynamic-screen');
  const subjectOpts = (S.subjects || []).map(s =>
    `<option value="${esc(s.id)}" ${S.searchSubjectId === s.id ? 'selected' : ''}>${esc(s.name)}</option>`
  ).join('');

  dynEl.innerHTML = `
    <div class="search-screen">
      <div class="search-header">
        <h2 class="search-title">🔍 문제 검색</h2>
      </div>
      <div class="search-bar-row">
        <input type="text" id="search-input" class="search-input"
          placeholder="키워드를 입력하세요 (예: 신뢰보호원칙, 소급입법)"
          value="${esc(S.searchQuery)}" autocomplete="off" />
        <button class="search-btn" id="search-btn">검색</button>
      </div>
      <div class="search-filter-row">
        <select id="search-subject" class="search-subject-select">
          <option value="">전체 과목</option>
          ${subjectOpts}
        </select>
      </div>
      <div id="search-results" class="search-results">
        ${S.searchResults.length === 0 && S.searchQuery
          ? '<div class="search-empty">검색 결과가 없습니다</div>'
          : S.searchResults.length === 0
          ? '<div class="search-hint">키워드를 입력하고 검색하세요</div>'
          : S.searchResults.map(q => `
            <div class="search-result-card" data-question-id="${esc(q.id)}">
              <div class="search-result-subject">${esc(q.subject_name || '')}</div>
              <div class="search-result-stem">${esc((q.stem || '').slice(0, 120))}${q.stem && q.stem.length > 120 ? '…' : ''}</div>
              <div class="search-result-meta">
                <span>${esc(q.source_name || '')}${q.source_year ? ` ${q.source_year}년` : ''}</span>
                <span>${q.question_number ? `${q.question_number}번` : ''}</span>
              </div>
            </div>
          `).join('')}
      </div>
    </div>
  `;

  const input = document.getElementById('search-input');
  const btn   = document.getElementById('search-btn');

  btn.addEventListener('click', () => performSearch(input.value.trim()));
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') performSearch(input.value.trim());
  });
  document.getElementById('search-subject').addEventListener('change', e => {
    S.searchSubjectId = e.target.value || null;
  });
  document.querySelectorAll('.search-result-card').forEach(card => {
    card.addEventListener('click', () => {
      // Navigate to question flashcard study
      const qid = card.dataset.questionId;
      S.returnTo = 'search';
      // Fetch the flashcard for this question and study it
      startStudyFromQuestion(qid);
    });
  });
}

async function performSearch(query) {
  if (!query && !S.searchSubjectId) return;
  S.searchQuery = query;
  showLoading();
  try {
    const params = new URLSearchParams({ limit: '30' });
    if (query) params.set('q', query);
    if (S.searchSubjectId) params.set('subject_id', S.searchSubjectId);
    const data = await api.get(`/questions/?${params}`);
    S.searchResults = (data.items || []).map(q => ({
      ...q,
      subject_name: (S.subjects.find(s => s.id === q.subject_id) || {}).name || '',
    }));
  } catch (e) {
    console.error(e);
    S.searchResults = [];
  }
  hideLoading();
  renderSearch();
}

async function startStudyFromQuestion(questionId) {
  showLoading();
  try {
    // Find the question-type flashcard for this question
    const cards = await api.get(`/flashcards/due?limit=1&subject_id=`);
    // Fallback: use the single-card restudy endpoint
    const fc = await api.get(`/cards/quick-scan?mode=newest&limit=1`);
    // Actually just use the question details to create a synthetic card
    const q = await api.get(`/questions/${questionId}`);
    hideLoading();
    // Navigate to study list to find this subject
    S.activeSubjectId = q.subject_id;
    startStudy(q.subject_id);
  } catch (e) {
    console.error(e);
    hideLoading();
  }
}


// ── Mock Exam Setup ────────────────────────────────────────────────────────────
function showMockSetup() {
  S.screen = 'mock-setup';
  hideBottomNav();
  document.getElementById('login-screen').hidden = true;
  renderMockSetup();
}

function renderMockSetup() {
  const subjects = S.subjects || [];
  const subjectOpts = subjects.map(s =>
    `<option value="${esc(s.id)}">${esc(s.name)}</option>`
  ).join('');

  const dynEl = document.getElementById('dynamic-screen');
  dynEl.innerHTML = `
    <div class="mock-setup-screen">
      <div class="mock-setup-header">
        <button class="btn-back" id="mock-setup-back">← 홈</button>
        <h2 class="mock-setup-title">📝 셀프 모의고사</h2>
        <div></div>
      </div>

      <div class="mock-setup-card">
        <div class="mock-setup-section">
          <div class="mock-setup-label">📚 과목 선택</div>
          <select id="mock-subject-select" class="mock-setup-select">
            <option value="">전체 과목</option>
            ${subjectOpts}
          </select>
        </div>

        <div class="mock-setup-section">
          <div class="mock-setup-label">📊 문항 수</div>
          <div class="mock-count-btns">
            <button class="mock-count-btn active" data-count="10">10문제</button>
            <button class="mock-count-btn" data-count="20">20문제</button>
            <button class="mock-count-btn" data-count="30">30문제</button>
          </div>
        </div>

        <div class="mock-setup-section">
          <div class="mock-setup-label">⏱️ 시간 제한</div>
          <div class="mock-count-btns">
            <button class="mock-time-btn active" data-minutes="0">제한 없음</button>
            <button class="mock-time-btn" data-minutes="10">10분</button>
            <button class="mock-time-btn" data-minutes="20">20분</button>
            <button class="mock-time-btn" data-minutes="30">30분</button>
          </div>
        </div>

        <button class="btn-start-mock" id="btn-start-mock">시험 시작</button>
      </div>
    </div>
  `;

  let selectedCount   = 10;
  let selectedMinutes = 0;

  document.getElementById('mock-setup-back').addEventListener('click', () => {
    showBottomNav(); setActiveTab('home'); showHome();
  });

  document.querySelectorAll('.mock-count-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.mock-count-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      selectedCount = parseInt(btn.dataset.count);
    });
  });

  document.querySelectorAll('.mock-time-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.mock-time-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      selectedMinutes = parseInt(btn.dataset.minutes);
    });
  });

  document.getElementById('btn-start-mock').addEventListener('click', () => {
    const subjectId = document.getElementById('mock-subject-select').value || null;
    startMockWithConfig(subjectId, selectedCount, selectedMinutes);
  });
}

async function startMockWithConfig(subjectId, count, timeLimitMinutes) {
  S.screen = 'mock-test';
  hideBottomNav();
  showLoading();
  try {
    const params = new URLSearchParams({ num_cards: count });
    if (subjectId) params.set('subject_id', subjectId);
    const cards = await api.get(`/mock/mock-test?${params}`);
    if (!cards || cards.length === 0) {
      showHome();
      alert('모의고사를 생성할 카드가 충분하지 않습니다.');
      return;
    }
    S.mockTest = {
      cards,
      index:      0,
      answers:    new Array(cards.length).fill(null),
      startTime:  Date.now(),
      timeLimit:  timeLimitMinutes * 60 * 1000,  // ms
      timerInterval: null,
    };
    hideLoading();
    renderMockTestQuestion();
    if (timeLimitMinutes > 0) _startMockTimer();
  } catch (err) {
    console.error(err);
    hideLoading();
    showHome();
    alert('모의고사를 시작하는 중 오류가 발생했습니다.');
  }
}

function _startMockTimer() {
  if (S.mockTest.timerInterval) clearInterval(S.mockTest.timerInterval);
  S.mockTest.timerInterval = setInterval(() => {
    if (!S.mockTest) { clearInterval(S.mockTest?.timerInterval); return; }
    const elapsed  = Date.now() - S.mockTest.startTime;
    const remaining = S.mockTest.timeLimit - elapsed;
    const el = document.getElementById('mock-timer');
    if (el) {
      if (remaining <= 0) {
        clearInterval(S.mockTest.timerInterval);
        el.textContent = '⏱ 00:00';
        showMockTestSummary();
      } else {
        const min = Math.floor(remaining / 60000);
        const sec = Math.floor((remaining % 60000) / 1000);
        el.textContent = `⏱ ${String(min).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
        if (remaining < 60000) el.style.color = 'var(--danger)';
      }
    }
  }, 1000);
}


// ── Init ──────────────────────────────────────────────────────────────────────
(async function init() {
  // FIX M-8: sync dark mode class on body (html class set by inline script in <head>)
  if (localStorage.getItem('dark_mode') === '1') {
    document.body.classList.add('dark-mode');
  }

  // Wire up login tab buttons
  document.getElementById('tab-login').addEventListener('click', () => {
    _authMode = 'login';
    renderLogin();
  });
  document.getElementById('tab-register').addEventListener('click', () => {
    _authMode = 'register';
    renderLogin();
  });
  document.getElementById('auth-form').addEventListener('submit', handleAuthSubmit);

  // Wire up bottom navigation
  document.getElementById('nav-home').addEventListener('click', showHome);
  document.getElementById('nav-study').addEventListener('click', showStudyList);
  document.getElementById('nav-search').addEventListener('click', showSearch);
  document.getElementById('nav-mypage').addEventListener('click', () => showMyPage());

  // FIX L-5: wire help modal close button
  document.getElementById('help-close').addEventListener('click', hideHelpModal);
  document.getElementById('help-modal').addEventListener('click', e => {
    if (e.target === document.getElementById('help-modal')) hideHelpModal();
  });

  // ── Feature 2: Inject Mini Dictionary panel + FAB ────────────────────────
  const _dictPanel = document.createElement('div');
  _dictPanel.id        = 'mini-dict-panel';
  _dictPanel.className = 'mini-dict-panel';
  _dictPanel.setAttribute('role', 'dialog');
  _dictPanel.setAttribute('aria-label', '미니 법률사전');
  _dictPanel.innerHTML = `
    <div class="mini-dict-header">
      <span class="mini-dict-title">📚 미니 법률사전</span>
      <button class="mini-dict-close" id="mini-dict-close" aria-label="닫기">✕</button>
    </div>
    <div class="mini-dict-search-row">
      <input type="search" id="mini-dict-input" class="mini-dict-input"
             placeholder="법령명, 조문번호, 판례번호 입력…"
             autocomplete="off" autocorrect="off" spellcheck="false" />
      <button id="mini-dict-search-btn" class="mini-dict-search-btn">검색</button>
    </div>
    <div class="dict-type-tabs">
      <button class="dict-tab active" data-type="all">전체</button>
      <button class="dict-tab" data-type="statute">📖 법령</button>
      <button class="dict-tab" data-type="precedent">⚖️ 판례</button>
    </div>
    <div id="mini-dict-results" class="mini-dict-results">
      <div class="dict-empty">검색어를 입력하세요</div>
    </div>
  `;
  document.getElementById('app').appendChild(_dictPanel);

  const _dictFab = document.createElement('button');
  _dictFab.id        = 'mini-dict-btn';
  _dictFab.className = 'mini-dict-fab';
  _dictFab.title     = '미니 법률사전 (법령·판례 검색)';
  _dictFab.setAttribute('aria-label', '미니 법률사전');
  _dictFab.textContent = '📚';
  document.body.appendChild(_dictFab);

  _dictFab.addEventListener('click', () => showMiniDict());
  document.getElementById('mini-dict-close').addEventListener('click', closeMiniDict);
  document.getElementById('mini-dict-search-btn').addEventListener('click', () => {
    performDictSearch(document.getElementById('mini-dict-input').value);
  });
  document.getElementById('mini-dict-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') performDictSearch(document.getElementById('mini-dict-input').value);
  });
  // Dict type tabs
  _dictPanel.querySelectorAll('.dict-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      S.dictType = btn.dataset.type;
      _dictPanel.querySelectorAll('.dict-tab').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const currentQ = document.getElementById('mini-dict-input')?.value || '';
      if (currentQ.trim()) performDictSearch(currentQ);
    });
  });
  // Close on outside click
  _dictPanel.addEventListener('click', e => { if (e.target === _dictPanel) closeMiniDict(); });

  if (tokens.exists()) {
    await showHome();
    // FIX L-3: update last_synced_at silently
    api.post('/users/me/sync', {}).catch(() => {});
  } else {
    showLogin();
  }
})();

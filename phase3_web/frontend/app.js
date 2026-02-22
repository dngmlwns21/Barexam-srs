'use strict';

// ── Constants ─────────────────────────────────────────────────────────────────
const API = '/api/v1';

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
      stem: oxCard.stem,
      explanation: oxCard.explanation,
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
      content: `[${oxCard.letter}] ${oxCard.statement}`,
      is_correct: oxCard.is_correct,
      choice_number: oxCard.choice_number,
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
        <div class="hero-daily-label">
          <span>오늘 진도</span>
          <span>${stats.reviewed_today} / ${total || stats.total_cards} 완료</span>
        </div>
        <div class="hero-progress-bar">
          <div class="hero-progress-fill" style="width:${pct}%"></div>
        </div>
      </div>

      ${statsStrip}
      ${deckSection}
    </div>
  `;

  document.getElementById('btn-logout').addEventListener('click', logout);
  document.getElementById('btn-dark-toggle-home').addEventListener('click', toggleDarkMode);
  document.getElementById('btn-cta')?.addEventListener('click', () => startMockStudy(null));
  document.querySelectorAll('.deck-row').forEach(btn => {
    btn.addEventListener('click', () => {
      const subj = btn.dataset.mockSubject;
      startMockStudy(subj === '전체 OX 카드' ? null : subj);
    });
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
      <div class="qs-section-header">⚡ 빠른 복습 (시험 직전 모드)</div>
      <div class="qs-mode-cards">
        <button class="qs-mode-btn" data-mode="failure">📉<span>오답 집중</span><small>틀린 적 많은 문제</small></button>
        <button class="qs-mode-btn" data-mode="newest">🆕<span>최신 문제</span><small>최근 추가된 문제</small></button>
        <button class="qs-mode-btn" data-mode="favorites">⭐<span>즐겨찾기</span><small>별표 표시 카드</small></button>
      </div>
    </div>
  `;

  document.querySelectorAll('.subject-card-v2').forEach(btn => {
    btn.addEventListener('click', () => {
      S.activeSubjectId = btn.dataset.id || null;
      startStudy();
    });
  });
  document.querySelectorAll('.qs-mode-btn').forEach(btn => {
    btn.addEventListener('click', () => showQuickScan(btn.dataset.mode));
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
    const choices = [...(q.choices || [])].sort((a, b) => a.choice_number - b.choice_number);
    choicesSection = `
      <div class="choices" id="choices">
        ${choices.map(c => `
          <button class="choice" data-num="${esc(c.choice_number)}">
            <span class="choice-num">${esc(c.choice_number)}</span>
            <span class="choice-text">${esc(c.content)}</span>
          </button>
        `).join('')}
      </div>
      <div class="keyboard-hint">키보드: 1–${choices.length} 선택</div>
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
        <div class="question-text">${fmt(q.stem)}</div>
        ${choicesSection}
      </div>
    </div>
  `;

  document.getElementById('btn-back').addEventListener('click',
    S.returnTo === 'mypage' ? () => showMyPage('history') : showHome);
  document.getElementById('btn-star').addEventListener('click', () => toggleStar(q.id, card.is_starred));
  document.getElementById('btn-help')?.addEventListener('click', showHelpModal);

  if (isOX) {
    document.getElementById('btn-ox-o').addEventListener('click', () => selectOX('O'));
    document.getElementById('btn-ox-x').addEventListener('click', () => selectOX('X'));
  } else {
    document.querySelectorAll('.choice').forEach(btn => {
      btn.addEventListener('click', () => selectChoice(parseInt(btn.dataset.num)));
    });
  }
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

  // Immediate reveal — answer known from card.choice.is_correct
  S.revealData = {
    answer:      S.card.choice.is_correct ? 'O' : 'X',
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

// ── RESULT ────────────────────────────────────────────────────────────────────
function renderResult() {
  const card  = S.card;
  const q     = card.question;
  const isOX  = card.type === 'choice_ox';

  // Correctness
  const correct = isOX
    ? (S.chosen === 'O') === card.choice.is_correct
    : S.chosen === S.revealData.answer;

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
          const cls = [
            'choice revealed',
            n === answer               ? 'choice-correct' : '',
            n === S.chosen && !correct ? 'choice-wrong'   : '',
          ].join(' ');
          return `
            <div class="${cls}">
              <span class="choice-num">${esc(n)}</span>
              <span class="choice-text">${esc(c.content)}</span>
              ${n === answer               ? '<span class="choice-mark">✓</span>' : ''}
              ${n === S.chosen && !correct ? '<span class="choice-mark">✗</span>' : ''}
            </div>
          `;
        }).join('')}
      </div>
    `;
  }

  // Explanation — always shown
  const explanation = isOX ? q.explanation : S.revealData.explanation;

  // Rich metadata from mock OX card
  const oxRaw = card._ox;
  const richMeta = (isOX && oxRaw) ? (() => {
    const imp = { A: '🔴 A 핵심', B: '🟡 B 표준', C: '⚪ C 주변' }[oxRaw.importance] || oxRaw.importance;
    const items = [];
    if (oxRaw.importance) items.push(`<span class="ox-meta-badge ox-importance-${oxRaw.importance}">${imp}</span>`);
    if (oxRaw.legal_provision) items.push(`<span class="ox-meta-badge ox-provision">📖 ${esc(oxRaw.legal_provision)}</span>`);
    if (oxRaw.precedent)       items.push(`<span class="ox-meta-badge ox-precedent">⚖️ ${esc(oxRaw.precedent)}</span>`);
    if (oxRaw.theory)          items.push(`<span class="ox-meta-badge ox-theory">💡 ${esc(oxRaw.theory)}</span>`);
    if (oxRaw.is_revised && oxRaw.revision_note) items.push(`<div class="ox-revision-note">⚠️ 개정: ${esc(oxRaw.revision_note)}</div>`);
    return items.length ? `<div class="ox-meta-row">${items.join('')}</div>` : '';
  })() : '';

  const explanationHtml = `
    <div class="explanation">
      <div class="explanation-title">해설</div>
      ${richMeta}
      <div class="explanation-text">${
        explanation
          ? fmt(explanation)
          : '<span class="explanation-placeholder">해설이 아직 제공되지 않은 문항입니다.</span>'
      }</div>
    </div>
  `;

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
  const ratingBtns = correct ? `
    <button class="btn-rating btn-hard" data-rating="3">어려움<small>${fmtInterval(calcNextInterval(sm2, 3, user))}</small></button>
    <button class="btn-rating btn-good" data-rating="4">알맞음<small>${fmtInterval(calcNextInterval(sm2, 4, user))}</small></button>
    <button class="btn-rating btn-easy" data-rating="5">쉬움<small>${fmtInterval(calcNextInterval(sm2, 5, user))}</small></button>
  ` : `
    <button class="btn-rating btn-again" data-rating="1">다시<small>${fmtInterval(calcNextInterval(sm2, 1, user))}</small></button>
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
        ${explanationHtml}
        ${tagsHtml}
        ${peerHtml}
        ${noteHtml}
      </div>

      <div class="rating-bar">
        <span class="rating-label">${correct ? '얼마나 잘 알았나요?' : '다시 학습합니다'}</span>
        <div class="rating-btns">${ratingBtns}</div>
      </div>
    </div>
  `;

  document.getElementById('btn-back').addEventListener('click',
    S.returnTo === 'mypage' ? () => showMyPage('history') : showHome);
  document.getElementById('btn-star').addEventListener('click', () => toggleStar(q.id, card.is_starred));
  document.getElementById('btn-save-note').addEventListener('click', () => saveNote(q.id));
  document.querySelectorAll('.btn-rating').forEach(btn => {
    btn.addEventListener('click', () => submitRating(parseInt(btn.dataset.rating)));
  });
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
    if (S.card && S.card.type === 'choice_ox') {
      if (e.key === 'o' || e.key === 'O') selectOX('O');
      else if (e.key === 'x' || e.key === 'X') selectOX('X');
    } else {
      const n = parseInt(e.key);
      if (n >= 1 && n <= 9) selectChoice(n);
    }

  } else if (S.screen === 'result') {
    const isOX  = S.card && S.card.type === 'choice_ox';
    const correct = isOX
      ? (S.chosen === 'O') === S.card.choice.is_correct
      : S.chosen === S.revealData?.answer;
    if (correct) {
      if (e.key === 'h' || e.key === 'H') submitRating(3);
      else if (e.key === 'g' || e.key === 'G' || e.key === ' ') submitRating(4);
      else if (e.key === 'e' || e.key === 'E') submitRating(5);
    } else {
      if (e.key === ' ' || e.key === 'Enter') submitRating(1);
    }

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
      const [stats, subjStats] = await Promise.all([
        api.get('/stats/'),
        api.get('/stats/subjects'),
      ]);
      S.stats        = stats;
      S.subjectStats = subjStats;
      S.streak       = stats.study_streak;
      data = { stats, subjStats };
    } else if (S.myPageTab === 'history') {
      // FIX M-10: reset offset when switching to history tab
      S.historyOffset = 0;
      const PAGE = 30;
      const logs = await api.get(`/reviews/history?limit=${PAGE + 1}&offset=0`);
      S.historyHasMore = logs.length > PAGE;
      data.logs = logs.slice(0, PAGE);
      S.historyOffset = PAGE;
    } else {
      data.user = S.user;
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
    { id: 'bookmarks', label: '설정' },
  ];
  const tabHtml = tabDefs.map(t => `
    <button class="mypage-tab-btn ${tab === t.id ? 'active' : ''}" data-tab="${t.id}">${t.label}</button>
  `).join('');

  let contentHtml = '';

  if (tab === 'stats') {
    const { stats } = data;
    if (!stats) {
      contentHtml = '<div class="review-empty">통계를 불러올 수 없습니다.</div>';
    } else {
      contentHtml = `
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
    const itemsHtml = logs.length === 0
      ? '<div class="review-empty">아직 학습 기록이 없어요</div>'
      : logs.map(log => {
          const stem  = (log.question_stem || '').slice(0, 60) +
                        ((log.question_stem || '').length > 60 ? '…' : '');
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
    const loadMoreHtml = S.historyHasMore
      ? `<button class="btn-load-more" id="btn-load-more">더 보기</button>`
      : '';
    contentHtml = `<div class="review-list" id="review-list-container">${itemsHtml}</div>${loadMoreHtml}`;
  } else {
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

  if (tab === 'bookmarks') {
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
  document.getElementById('nav-mypage').addEventListener('click', () => showMyPage());

  // FIX L-5: wire help modal close button
  document.getElementById('help-close').addEventListener('click', hideHelpModal);
  document.getElementById('help-modal').addEventListener('click', e => {
    if (e.target === document.getElementById('help-modal')) hideHelpModal();
  });

  if (tokens.exists()) {
    await showHome();
    // FIX L-3: update last_synced_at silently
    api.post('/users/me/sync', {}).catch(() => {});
  } else {
    showLogin();
  }
})();

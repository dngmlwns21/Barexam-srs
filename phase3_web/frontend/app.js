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
  screen:         'login',  // 'login' | 'home' | 'study' | 'result' | 'done'
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

// ── Dark mode ─────────────────────────────────────────────────────────────────
function toggleDarkMode() {
  const dark = document.body.classList.toggle('dark-mode');
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

// ── LOGIN / REGISTER ──────────────────────────────────────────────────────────
let _authMode = 'login'; // 'login' | 'register'

function showLogin() {
  S.screen = 'login';
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
    const [stats, subjStats, subjects, user] = await Promise.all([
      api.get('/stats/'),
      api.get('/stats/subjects'),
      api.get('/subjects/'),
      api.get('/users/me'),
    ]);
    S.stats        = stats;
    S.subjectStats = subjStats;
    S.subjects     = subjects;
    S.streak       = stats.study_streak;
    S.user         = user;
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

  hideLoading();
  renderHome();
}

function renderHome() {
  const { stats, subjects, subjectStats, activeSubjectId, streak } = S;

  // Find stats for the active subject (or overall)
  let dueCount;
  if (activeSubjectId) {
    const ss = subjectStats.find(s => s.subject_id === activeSubjectId);
    dueCount = ss ? ss.due : 0;
  } else {
    dueCount = stats.due_today;
  }

  const dynEl = document.getElementById('dynamic-screen');
  dynEl.innerHTML = `
    <div class="home">
      <div class="home-header-row">
        <div>
          <h1 style="font-size:2rem;font-weight:800;color:var(--primary);letter-spacing:-0.5px;">⚖️ 변호사시험 SRS</h1>
          <p class="subtitle" style="margin-top:4px;color:var(--text-muted);font-size:.9rem;">
            SM-2 스페이스드 리피티션 · ${stats.total_cards.toLocaleString()}카드
          </p>
        </div>
        <div style="display:flex;align-items:center;gap:10px;">
          ${streak > 0 ? `<div class="streak-badge">🔥 ${streak}일 연속</div>` : ''}
          <button class="btn-dark-toggle" id="btn-dark-toggle-home" title="다크 모드">
            ${document.body.classList.contains('dark-mode') ? '🌙' : '☀️'}
          </button>
          <button class="btn-my-page" id="btn-my-page">내 기록</button>
          <button class="btn-logout" id="btn-logout">로그아웃</button>
        </div>
      </div>

      <div class="stats-grid">
        <div class="stat-card stat-new">
          <div class="stat-num">${stats.due_today.toLocaleString()}</div>
          <div class="stat-label">오늘 복습</div>
        </div>
        <div class="stat-card stat-due">
          <div class="stat-num">${stats.reviewed_today.toLocaleString()}</div>
          <div class="stat-label">오늘 완료</div>
        </div>
        <div class="stat-card stat-done">
          <div class="stat-num">${stats.correct_today.toLocaleString()}</div>
          <div class="stat-label">정답</div>
        </div>
        <div class="stat-card stat-total">
          <div class="stat-num">${stats.accuracy_7d.toFixed(1)}%</div>
          <div class="stat-label">7일 정확도</div>
        </div>
      </div>

      <div class="subjects">
        <div class="subjects-label">과목 선택</div>
        <div class="subject-list">
          <button class="subject-card ${!activeSubjectId ? 'active' : ''}" data-id="">
            <div class="subject-card-name">전체</div>
            <div class="subject-card-stats">
              <span class="subject-card-due${stats.due_today > 0 ? ' has-due' : ''}">
                ${stats.due_today.toLocaleString()} 예정
              </span>
              <span class="subject-card-total">${stats.total_cards.toLocaleString()} 카드</span>
            </div>
          </button>
          ${subjects.map(s => {
            const ss = subjectStats.find(x => x.subject_id === s.id);
            const due = ss ? ss.due : 0;
            const total = s.total_questions || 0;
            return `
              <button class="subject-card ${activeSubjectId === s.id ? 'active' : ''}" data-id="${esc(s.id)}">
                <div class="subject-card-name">${esc(s.name)}</div>
                <div class="subject-card-stats">
                  <span class="subject-card-due${due > 0 ? ' has-due' : ''}">
                    ${due.toLocaleString()} 예정
                  </span>
                  <span class="subject-card-total">${total.toLocaleString()} 카드</span>
                </div>
              </button>
            `;
          }).join('')}
        </div>
      </div>

      <button class="btn-start${dueCount === 0 ? ' disabled' : ''}"
              id="btn-start" ${dueCount === 0 ? 'disabled' : ''}>
        ${dueCount === 0
          ? '✓ 오늘 학습 완료!'
          : `학습 시작 (${dueCount.toLocaleString()}장)`}
      </button>
    </div>
  `;

  document.getElementById('btn-logout').addEventListener('click', logout);
  document.getElementById('btn-dark-toggle-home').addEventListener('click', toggleDarkMode);
  document.getElementById('btn-my-page').addEventListener('click', showMyPage);

  document.querySelectorAll('.subject-card').forEach(btn => {
    btn.addEventListener('click', async () => {
      S.activeSubjectId = btn.dataset.id || null;
      await showHome();
    });
  });

  document.getElementById('btn-start')?.addEventListener('click', startStudy);
}

// ── STUDY ─────────────────────────────────────────────────────────────────────
async function startStudy() {
  S.sessionDone = 0;
  await fetchNextCard();
}

async function fetchNextCard() {
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
      <div class="ox-choice-text">
        <span class="choice-num">${esc(c.choice_number)}</span>
        <span>${esc(c.content)}</span>
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
          <div class="session-count">${S.sessionDone}개 완료</div>
        </div>
      </div>

      <div class="question-card">
        ${warningBadge}
        <div class="question-text">${fmt(q.stem)}</div>
        ${choicesSection}
      </div>
    </div>
  `;

  document.getElementById('btn-back').addEventListener('click', showHome);
  document.getElementById('btn-star').addEventListener('click', () => toggleStar(q.id, card.is_starred));

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
        <div class="ox-choice-text">
          <span class="choice-num">${esc(card.choice.choice_number)}</span>
          <span>${esc(card.choice.content)}</span>
        </div>
        <div class="ox-actual ${card.choice.is_correct ? 'ox-correct' : 'ox-wrong'}">
          이 선택지는 <strong>${card.choice.is_correct ? 'O (맞음)' : 'X (틀림)'}</strong>입니다
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

  // Explanation — always shown (placeholder when null)
  const explanation = isOX ? q.explanation : S.revealData.explanation;
  const explanationHtml = `
    <div class="explanation">
      <div class="explanation-title">해설</div>
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

  document.getElementById('btn-back').addEventListener('click', showHome);
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

  // answer_given is only meaningful for MCQ (integer 1-5), not for O/X
  const body = { rating };
  if (!isOX && typeof S.chosen === 'number') {
    body.answer_given = S.chosen;
  }

  try {
    await api.post(`/reviews/${flashcardId}`, body);
  } catch (err) {
    console.error(err);
  }

  S.sessionDone++;
  await fetchNextCard();
}

// ── DONE ──────────────────────────────────────────────────────────────────────
function renderDone() {
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

// ── Keyboard shortcuts ────────────────────────────────────────────────────────
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

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
    if (e.key === 'Enter') document.getElementById('btn-start')?.click();
  }
});

// ── My Page ───────────────────────────────────────────────────────────────────
async function showMyPage() {
  S.screen = 'mypage';
  showLoading();
  document.getElementById('login-screen').hidden = true;
  let logs = [];
  try { logs = await api.get('/reviews/history?limit=30'); } catch(e) {}
  hideLoading();

  function relTime(iso) {
    const min = Math.floor((Date.now() - new Date(iso)) / 60000);
    if (min < 1) return '방금';
    if (min < 60) return `${min}분 전`;
    const hr = Math.floor(min/60);
    if (hr < 24) return `${hr}시간 전`;
    const d = Math.floor(hr/24);
    return d < 7 ? `${d}일 전` : `${Math.floor(d/7)}주 전`;
  }

  const itemsHtml = logs.length === 0
    ? '<div class="review-empty">아직 학습 기록이 없어요</div>'
    : logs.map(log => {
        const stem = (log.question_stem||'').slice(0,60) + ((log.question_stem||'').length>60?'…':'');
        const badge = log.card_type==='choice_ox'
          ? '<span class="review-badge review-badge-ox">O/X</span>'
          : '<span class="review-badge review-badge-mcq">MCQ</span>';
        const dots = '●'.repeat(Math.min(log.rating,5))+'○'.repeat(5-Math.min(log.rating,5));
        return `<div class="review-item">
          ${badge}
          <div class="review-stem">${esc(stem||'(문제 정보 없음)')}</div>
          <div class="review-meta">
            <span class="review-correct ${log.was_correct?'correct':'wrong'}">${log.was_correct?'✓':'✗'}</span>
            <span class="review-rating">${dots}</span>
            <span class="review-time">${relTime(log.reviewed_at)}</span>
          </div></div>`;
      }).join('');

  document.getElementById('dynamic-screen').innerHTML = `
    <div class="my-page">
      <div class="my-page-header">
        <button class="btn-back" id="btn-back-mypage">← 홈</button>
        <h2 class="my-page-title">내 기록</h2>
        ${S.streak>0?`<span class="streak-badge">🔥 ${S.streak}일</span>`:''}
      </div>
      <div class="review-list">${itemsHtml}</div>
    </div>`;
  document.getElementById('btn-back-mypage').addEventListener('click', showHome);
}

// ── Init ──────────────────────────────────────────────────────────────────────
(async function init() {
  // Restore dark mode preference
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

  if (tokens.exists()) {
    await showHome();
  } else {
    showLogin();
  }
})();

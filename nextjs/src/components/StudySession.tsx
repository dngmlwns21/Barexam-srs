"use client";

import { useEffect, useState, useCallback } from "react";
import { useSearchParams, useRouter } from "next/navigation";

interface OXCard {
  raw_id: string;
  subject: string;
  year: number | null;
  source: string;
  question_number: number;
  stem: string;
  overall_explanation: string | null;
  letter: string;
  choice_number: number;
  statement: string;
  is_correct: boolean;
  legal_basis: string | null;
  case_citation: string | null;
  theory: string | null;
  is_revised: boolean;
  revision_note: string | null;
  importance: string;
  explanation: string;
  is_outdated: boolean;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const IMPORTANCE_STYLE: Record<string, string> = {
  A: "bg-red-100 text-red-700 border border-red-200",
  B: "bg-yellow-100 text-yellow-700 border border-yellow-200",
  C: "bg-gray-100 text-gray-600 border border-gray-200",
};

const IMPORTANCE_LABEL: Record<string, string> = {
  A: "A 핵심",
  B: "B 표준",
  C: "C 주변",
};

// ── Grade button config ──────────────────────────────────────────────────────
const GRADE_BUTTONS = [
  { rating: 1, label: "다시",   sub: "Again", cls: "bg-red-500 text-white hover:bg-red-600"   },
  { rating: 2, label: "어려움", sub: "Hard",  cls: "bg-gray-500 text-white hover:bg-gray-600"  },
  { rating: 3, label: "보통",   sub: "Good",  cls: "bg-green-500 text-white hover:bg-green-600" },
  { rating: 4, label: "쉬움",   sub: "Easy",  cls: "bg-blue-500 text-white hover:bg-blue-600"  },
] as const;

// ── Component ────────────────────────────────────────────────────────────────
export default function StudySession() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const subject = searchParams.get("subject") ?? undefined;

  const [cards, setCards] = useState<OXCard[]>([]);
  const [index, setIndex] = useState(0);
  const [revealed, setRevealed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [done, setDone] = useState(false);

  useEffect(() => {
    const url = subject
      ? `${API_BASE}/api/v1/mock/cards?subject=${encodeURIComponent(subject)}&limit=500`
      : `${API_BASE}/api/v1/mock/cards?limit=500`;
    fetch(url)
      .then((r) => r.json())
      .then((data: any[]) => {
        const mappedData = data.map(item => ({
          ...item,
          legal_basis: item.legal_provision,
          case_citation: item.precedent,
        }));
        setCards(mappedData as OXCard[]);
      })
      .finally(() => setLoading(false));
  }, [subject]);

  const card = cards[index];

  const grade = useCallback(
    (_rating: number) => {
      // TODO: POST /api/v1/reviews/{flashcard_id} { rating: _rating }
      const next = index + 1;
      if (next >= cards.length) {
        setDone(true);
      } else {
        setIndex(next);
        setRevealed(false);
      }
    },
    [index, cards.length]
  );

  // Keyboard shortcuts: Space = reveal, 1–4 = grade
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === " " && !revealed) {
        e.preventDefault();
        setRevealed(true);
        return;
      }
      if (revealed) {
        const n = parseInt(e.key);
        if (n >= 1 && n <= 4) grade(n);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [revealed, grade]);

  // ── Loading ────────────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center bg-gray-50">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-blue-500 border-t-transparent" />
      </div>
    );
  }

  // ── Done / Empty ───────────────────────────────────────────────────────────
  if (done || cards.length === 0) {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-4 bg-gray-50">
        <span className="text-6xl">{cards.length === 0 ? "📭" : "🎉"}</span>
        <h2 className="text-xl font-bold text-gray-800">
          {cards.length === 0 ? "카드 없음" : "학습 완료!"}
        </h2>
        <p className="text-sm text-gray-500">
          {cards.length === 0
            ? "선택한 과목에 카드가 없습니다."
            : `${cards.length}개 카드를 모두 학습했습니다.`}
        </p>
        <button
          onClick={() => router.push("/")}
          className="mt-2 rounded-2xl bg-blue-500 px-8 py-3 text-sm font-bold text-white hover:bg-blue-600 transition-colors"
        >
          홈으로
        </button>
      </div>
    );
  }

  const progress = (index / cards.length) * 100;

  // ── Main render ────────────────────────────────────────────────────────────
  return (
    <div className="flex h-screen flex-col bg-gray-50">
      {/* Progress bar */}
      <div className="h-1 bg-gray-200">
        <div
          className="h-full bg-blue-500 transition-all duration-300"
          style={{ width: `${progress}%` }}
        />
      </div>

      {/* Top bar */}
      <div className="flex items-center justify-between px-4 py-3 bg-white border-b border-gray-100">
        <button
          onClick={() => router.push("/")}
          className="text-sm text-gray-500 hover:text-gray-800 transition-colors"
        >
          ← 홈
        </button>
        <span className="text-sm font-semibold text-gray-500">
          {index + 1} <span className="text-gray-300">/</span> {cards.length}
        </span>
        <div className="w-10" />
      </div>

      {/* Scrollable card area */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
        {/* ── Outdated law banner ────────────────────────────────────────── */}
        {card.is_outdated && (
          <div className="rounded-2xl border border-yellow-300 bg-yellow-50 px-4 py-3">
            <div className="flex items-start gap-2">
              <span className="text-xl leading-none">⚠️</span>
              <div>
                <p className="text-sm font-bold text-yellow-800">법령 개정 주의</p>
                <p className="text-xs text-yellow-700 mt-0.5 leading-relaxed">
                  이 문제의 법령 또는 판례가 변경되어 현행법과 다를 수 있습니다.
                </p>
              </div>
            </div>
          </div>
        )}

        {/* ── Meta badges ───────────────────────────────────────────────── */}
        <div className="flex flex-wrap gap-1.5">
          <span className="rounded-full bg-indigo-100 px-3 py-1 text-xs font-semibold text-indigo-700">
            {card.subject}
          </span>
          {card.year && (
            <span className="rounded-full bg-gray-100 px-3 py-1 text-xs font-medium text-gray-600">
              {card.year}년
            </span>
          )}
          <span className="rounded-full bg-gray-100 px-3 py-1 text-xs font-medium text-gray-600">
            {card.source} Q{card.question_number}
          </span>
          <span
            className={`rounded-full px-3 py-1 text-xs font-bold ${IMPORTANCE_STYLE[card.importance] ?? IMPORTANCE_STYLE.B}`}
          >
            {IMPORTANCE_LABEL[card.importance] ?? card.importance}
          </span>
          {card.is_revised && (
            <span className="rounded-full border border-orange-200 bg-orange-50 px-3 py-1 text-xs font-semibold text-orange-700">
              개정 가능
            </span>
          )}
        </div>

        {/* ── FRONT: Statement ──────────────────────────────────────────── */}
        <div className="rounded-2xl bg-white p-6 shadow-sm">
          <p className="mb-3 text-[11px] font-bold uppercase tracking-widest text-gray-400">
            O / X 판단
          </p>
          <p className="text-lg font-medium leading-relaxed text-gray-900">
            {card.statement}
          </p>
        </div>

        {/* ── Show Answer button ────────────────────────────────────────── */}
        {!revealed && (
          <button
            onClick={() => setRevealed(true)}
            className="w-full rounded-2xl bg-gray-800 py-4 text-sm font-bold text-white hover:bg-gray-900 active:scale-[.98] transition-all"
          >
            정답 보기{" "}
            <span className="ml-1 text-xs text-gray-400">(Space)</span>
          </button>
        )}

        {/* ── BACK: Answer ──────────────────────────────────────────────── */}
        {revealed && (
          <div className="space-y-3">
            {/* Verdict */}
            <div
              className={`flex items-center justify-center rounded-2xl py-6 shadow-sm ${
                card.is_correct ? "bg-emerald-500" : "bg-red-500"
              }`}
            >
              <span className="text-6xl font-black text-white drop-shadow">
                {card.is_correct ? "O" : "X"}
              </span>
            </div>

            {/* Explanation */}
            <div className="rounded-2xl bg-white p-5 shadow-sm">
              <p className="mb-2 text-[11px] font-bold uppercase tracking-widest text-gray-400">
                해설
              </p>
              <p className="text-sm leading-relaxed text-gray-700">
                {card.explanation}
              </p>
            </div>

            {/* Union Textbook Style citations */}
            {(card.legal_basis || card.case_citation || card.theory) && (
              <div className="rounded-2xl bg-white p-5 shadow-sm space-y-2.5">
                <p className="text-[11px] font-bold uppercase tracking-widest text-gray-400">
                  근거
                </p>
                {card.legal_basis && (
                  <div className="flex items-start gap-2">
                    <span className="mt-0.5 shrink-0 rounded-md bg-blue-100 px-1.5 py-0.5 text-xs font-bold text-blue-700">
                      조문
                    </span>
                    <span className="text-sm text-gray-700">
                      {card.legal_basis}
                    </span>
                  </div>
                )}
                {card.case_citation && (
                  <div className="flex items-start gap-2">
                    <span className="mt-0.5 shrink-0 rounded-md bg-purple-100 px-1.5 py-0.5 text-xs font-bold text-purple-700">
                      판례
                    </span>
                    <span className="text-sm text-gray-700">
                      {card.case_citation}
                    </span>
                  </div>
                )}
                {card.theory && (
                  <div className="flex items-start gap-2">
                    <span className="mt-0.5 shrink-0 rounded-md bg-gray-100 px-1.5 py-0.5 text-xs font-bold text-gray-600">
                      학설
                    </span>
                    <span className="text-sm text-gray-700">{card.theory}</span>
                  </div>
                )}
              </div>
            )}

            {/* Revision note */}
            {card.is_revised && card.revision_note && (
              <div className="rounded-2xl border border-orange-200 bg-orange-50 p-4">
                <p className="text-xs font-bold text-orange-700 mb-1">
                  ⚠️ 개정 사항
                </p>
                <p className="text-sm leading-relaxed text-orange-800">
                  {card.revision_note}
                </p>
              </div>
            )}

            {/* Overall explanation (collapsible hint) */}
            {card.overall_explanation && (
              <details className="rounded-2xl bg-white shadow-sm">
                <summary className="cursor-pointer select-none px-5 py-4 text-[11px] font-bold uppercase tracking-widest text-gray-400 hover:text-gray-600 transition-colors">
                  문제 전체 해설 펼치기
                </summary>
                <div className="px-5 pb-5">
                  <p className="text-sm leading-relaxed text-gray-600">
                    {card.overall_explanation}
                  </p>
                </div>
              </details>
            )}

            {/* Spacer so content clears the action bar */}
            <div className="h-20" />
          </div>
        )}
      </div>

      {/* AI Tutor FAB */}
      <button
        onClick={() => alert("AI Tutor (Coming Soon!)")}
        className="fixed bottom-24 right-6 z-10 h-14 w-14 rounded-full bg-indigo-600 text-white shadow-lg flex items-center justify-center hover:bg-indigo-700 transition-all transform hover:scale-105"
        title="AI Tutor"
      >
        <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707m12.728 0l-.707.707M12 21v-1m0-16a9 9 0 110 18 9 9 0 010-18z" />
        </svg>
        <span className="sr-only">AI Tutor</span>
      </button>

      {/* ── Bottom action bar ─────────────────────────────────────────────── */}
      {revealed && (
        <div className="border-t border-gray-200 bg-white px-4 py-3 safe-area-bottom">
          <div className="grid grid-cols-4 gap-2">
            {GRADE_BUTTONS.map(({ rating, label, sub, cls }) => (
              <button
                key={rating}
                onClick={() => grade(rating)}
                className={`flex flex-col items-center rounded-2xl py-3 transition-all active:scale-95 ${cls}`}
              >
                <span className="text-sm font-bold">{label}</span>
                <span className="text-[10px] opacity-80">{sub}</span>
              </button>
            ))}
          </div>
          <p className="mt-2 text-center text-[10px] text-gray-400">
            키보드: 1 다시 · 2 어려움 · 3 보통 · 4 쉬움
          </p>
        </div>
      )}
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

interface Deck {
  subject: string;
  new_count: number;
  learning_count: number;
  review_count: number;
  total: number;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export default function Dashboard() {
  const router = useRouter();
  const [decks, setDecks] = useState<Deck[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API_BASE}/api/v1/mock/decks`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data: Deck[]) => setDecks(data))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const totalCards = decks.reduce((a, d) => a + d.total, 0);
  const totalNew = decks.reduce((a, d) => a + d.new_count, 0);
  const totalLearning = decks.reduce((a, d) => a + d.learning_count, 0);
  const totalReview = decks.reduce((a, d) => a + d.review_count, 0);

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center bg-gray-50">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-blue-500 border-t-transparent" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-screen items-center justify-center bg-gray-50">
        <div className="rounded-2xl bg-white p-8 shadow text-center">
          <p className="text-red-500 font-semibold">API 연결 실패</p>
          <p className="text-sm text-gray-500 mt-1">{error}</p>
          <p className="text-xs text-gray-400 mt-3">
            uvicorn phase4_api.main:app --reload --port 8000
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="mx-auto max-w-2xl px-4 py-8">
        {/* Header */}
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-gray-900">변호사시험 SRS</h1>
          <p className="mt-1 text-sm text-gray-500">
            {totalCards}개 카드 · {decks.length}개 과목
          </p>
        </div>

        {/* Summary strip */}
        <div className="mb-4 grid grid-cols-3 gap-3">
          <div className="rounded-xl bg-blue-50 p-3 text-center">
            <p className="text-2xl font-black text-blue-500">{totalNew}</p>
            <p className="text-xs text-blue-400 font-medium mt-0.5">신규</p>
          </div>
          <div className="rounded-xl bg-red-50 p-3 text-center">
            <p className="text-2xl font-black text-red-500">{totalLearning}</p>
            <p className="text-xs text-red-400 font-medium mt-0.5">학습중</p>
          </div>
          <div className="rounded-xl bg-green-50 p-3 text-center">
            <p className="text-2xl font-black text-green-500">{totalReview}</p>
            <p className="text-xs text-green-400 font-medium mt-0.5">복습</p>
          </div>
        </div>

        {/* Deck table */}
        <div className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
          {/* Column headers */}
          <div className="flex items-center border-b border-gray-100 bg-gray-50 px-4 py-2">
            <span className="flex-1 text-xs font-semibold text-gray-500">과목</span>
            <span className="w-12 text-right text-xs font-semibold text-blue-500">신규</span>
            <span className="w-12 text-right text-xs font-semibold text-red-500">학습</span>
            <span className="w-12 text-right text-xs font-semibold text-green-500">복습</span>
          </div>

          {decks.map((deck, i) => {
            const hasCards =
              deck.new_count + deck.learning_count + deck.review_count > 0;
            return (
              <button
                key={deck.subject}
                onClick={() =>
                  router.push(`/study?subject=${encodeURIComponent(deck.subject)}`)
                }
                disabled={!hasCards}
                className={[
                  "flex w-full items-center px-4 py-3.5 text-left transition-colors",
                  i > 0 ? "border-t border-gray-100" : "",
                  hasCards
                    ? "hover:bg-blue-50 active:bg-blue-100 cursor-pointer"
                    : "opacity-40 cursor-not-allowed",
                ].join(" ")}
              >
                <span className="flex-1 text-sm font-medium text-gray-800">
                  {deck.subject}
                </span>
                <span className="w-12 text-right text-sm font-bold text-blue-500">
                  {deck.new_count > 0 ? deck.new_count : ""}
                </span>
                <span className="w-12 text-right text-sm font-bold text-red-500">
                  {deck.learning_count > 0 ? deck.learning_count : ""}
                </span>
                <span className="w-12 text-right text-sm font-bold text-green-500">
                  {deck.review_count > 0 ? deck.review_count : ""}
                </span>
              </button>
            );
          })}

          {decks.length === 0 && (
            <div className="py-16 text-center text-sm text-gray-400">
              카드가 없습니다
            </div>
          )}
        </div>

        {/* Study all CTA */}
        {totalNew + totalLearning + totalReview > 0 && (
          <button
            onClick={() => router.push("/study")}
            className="mt-4 w-full rounded-2xl bg-blue-500 py-4 text-sm font-bold text-white shadow-sm hover:bg-blue-600 active:bg-blue-700 transition-colors"
          >
            전체 학습 시작 ({totalNew + totalLearning + totalReview}개)
          </button>
        )}
      </div>
    </div>
  );
}

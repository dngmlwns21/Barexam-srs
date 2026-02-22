"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

interface Deck {
  subject_id: string | null;
  subject_name: string;
  new_count: number;
  learning_count: number;
  review_count: number;
  total_cards: number;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export default function Dashboard() {
  const router = useRouter();
  const [decks, setDecks] = useState<Deck[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API_BASE}/api/v1/dashboard/deck-stats`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data: Deck[]) => setDecks(data))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const overallDeck = decks.find((d) => d.subject_id === null);
  const subjectDecks = decks.filter((d) => d.subject_id !== null);

  const totalNew = overallDeck?.new_count ?? 0;
  const totalLearning = overallDeck?.learning_count ?? 0;
  const totalReview = overallDeck?.review_count ?? 0;
  const totalCards = totalNew + totalLearning + totalReview; // Adjusted to reflect actual counts

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
            uvicorn backend.main:app --reload --port 8000
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
            {totalCards}개 카드 · {subjectDecks.length}개 과목
          </p>
        </div>

        {/* Anki-style Summary strip */}
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

          {/* Overall Deck (Study All) */}
          <button
            onClick={() => router.push("/study")}
            disabled={totalNew + totalLearning + totalReview === 0}
            className="flex w-full items-center px-4 py-3.5 text-left font-bold transition-colors border-b border-gray-100 hover:bg-blue-50 active:bg-blue-100 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <span className="flex-1 text-sm text-gray-900">전체 과목</span>
            <span className="w-12 text-right text-sm font-bold text-blue-500">
              {totalNew > 0 ? totalNew : ""}
            </span>
            <span className="w-12 text-right text-sm font-bold text-red-500">
              {totalLearning > 0 ? totalLearning : ""}
            </span>
            <span className="w-12 text-right text-sm font-bold text-green-500">
              {totalReview > 0 ? totalReview : ""}
            </span>
          </button>

          {/* Subject Decks */}
          {subjectDecks.map((deck) => {
            const hasCards =
              deck.new_count + deck.learning_count + deck.review_count > 0;
            return (
              <button
                key={deck.subject_id}
                onClick={() =>
                  router.push(
                    `/study?subject=${encodeURIComponent(deck.subject_name)}`,
                  )
                }
                disabled={!hasCards}
                className="flex w-full items-center px-4 py-3.5 text-left transition-colors border-t border-gray-100 hover:bg-blue-50 active:bg-blue-100 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <span className="flex-1 text-sm font-medium text-gray-800">
                  {deck.subject_name}
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
      </div>
    </div>
  );
}

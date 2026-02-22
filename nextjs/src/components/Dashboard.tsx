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
          <p className="text-red-500 font-semibold">API Error</p>
          <p className="text-sm text-gray-500 mt-1">{error}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50 font-sans">
      <div className="mx-auto max-w-3xl px-4 py-8">
        <header className="mb-8 text-center">
          <h1 className="text-3xl font-extrabold text-gray-900 tracking-tight">Bar Exam SRS</h1>
          <p className="mt-2 text-sm text-gray-500">
            Anki-style Spaced Repetition for Legal Professionals
          </p>
        </header>

        {/* Deck List */}
        <div className="overflow-hidden rounded-xl bg-white shadow-sm border border-gray-200">
          <div className="grid grid-cols-[1fr_repeat(3,minmax(60px,auto))] items-center border-b border-gray-100 bg-gray-50 px-6 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">
            <span className="text-left">Deck</span>
            <span className="text-center text-blue-600">New</span>
            <span className="text-center text-red-600">Lrn</span>
            <span className="text-center text-green-600">Rev</span>
          </div>

          {/* Overall Deck */}
          <div 
            onClick={() => router.push("/study")}
            className="grid grid-cols-[1fr_repeat(3,minmax(60px,auto))] items-center px-6 py-4 cursor-pointer hover:bg-gray-50 transition-colors border-b border-gray-100 group"
          >
            <span className="font-bold text-gray-900 group-hover:text-blue-600 transition-colors">All Subjects</span>
            <span className="text-center font-mono text-sm font-bold text-blue-500">
              {overallDeck?.new_count || "-"}
            </span>
            <span className="text-center font-mono text-sm font-bold text-red-500">
              {overallDeck?.learning_count || "-"}
            </span>
            <span className="text-center font-mono text-sm font-bold text-green-500">
              {overallDeck?.review_count || "-"}
            </span>
          </div>

          {/* Subject Decks */}
          {subjectDecks.map((deck) => (
            <div
              key={deck.subject_id}
              onClick={() => router.push(`/study?subject=${encodeURIComponent(deck.subject_name)}`)}
              className="grid grid-cols-[1fr_repeat(3,minmax(60px,auto))] items-center px-6 py-4 cursor-pointer hover:bg-gray-50 transition-colors border-b border-gray-100 last:border-0 group"
            >
              <span className="font-medium text-gray-700 group-hover:text-blue-600 transition-colors">
                {deck.subject_name}
              </span>
              <span className="text-center font-mono text-sm text-blue-500">
                {deck.new_count > 0 ? deck.new_count : "-"}
              </span>
              <span className="text-center font-mono text-sm text-red-500">
                {deck.learning_count > 0 ? deck.learning_count : "-"}
              </span>
              <span className="text-center font-mono text-sm text-green-500">
                {deck.review_count > 0 ? deck.review_count : "-"}
              </span>
            </div>
          ))}
          
          {decks.length === 0 && (
            <div className="p-8 text-center text-gray-400">No decks available.</div>
          )}
        </div>
      </div>
    </div>
  );
}

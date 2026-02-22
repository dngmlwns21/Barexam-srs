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
  explanation_core: string | null;
  keywords: string[];
  theory: string | null;
  is_revised: boolean;
  revision_note: string | null;
  importance: string;
  explanation: string;
  is_outdated: boolean;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const IMPORTANCE_STYLE: Record<string, string> = {
  A: "bg-red-100 text-red-700 ring-1 ring-red-200",
  B: "bg-yellow-100 text-yellow-700 ring-1 ring-yellow-200",
  C: "bg-gray-100 text-gray-600 ring-1 ring-gray-200",
};

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
      ? `${API_BASE}/api/v1/mock/cards?subject=${encodeURIComponent(subject)}&limit=100`
      : `${API_BASE}/api/v1/mock/cards?limit=100`;
    fetch(url)
      .then((r) => r.json())
      .then((data: OXCard[]) => setCards(data))
      .finally(() => setLoading(false));
  }, [subject]);

  const card = cards[index];

  const grade = useCallback((rating: number) => {
    // In a real app, send rating to backend here
    const next = index + 1;
    if (next >= cards.length) {
      setDone(true);
    } else {
      setIndex(next);
      setRevealed(false);
    }
  }, [index, cards.length]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === " " && !revealed) {
        e.preventDefault();
        setRevealed(true);
      } else if (revealed) {
        if (e.key === "1") grade(1); // Again
        if (e.key === "2") grade(2); // Hard
        if (e.key === "3") grade(3); // Good
        if (e.key === "4") grade(4); // Easy
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [revealed, grade]);

  const handleAiExplain = () => {
    // Placeholder for AI Tutor
    alert("AI Tutor: Analyzing this legal principle for you...");
  };

  if (loading) return <div className="flex h-screen items-center justify-center"><div className="animate-spin h-8 w-8 border-4 border-blue-600 rounded-full border-t-transparent"></div></div>;
  if (done) return <div className="flex h-screen items-center justify-center flex-col"><h1 className="text-2xl font-bold">Session Complete</h1><button onClick={() => router.push("/")} className="mt-4 px-6 py-2 bg-blue-600 text-white rounded-lg">Home</button></div>;
  if (!card) return <div className="flex h-screen items-center justify-center">No cards found</div>;

  return (
    <div className="flex flex-col h-screen bg-gray-100 max-w-2xl mx-auto shadow-2xl overflow-hidden relative">
      {/* Header / Progress */}
      <div className="bg-white px-4 py-3 border-b border-gray-200 flex justify-between items-center z-10">
        <span className="text-xs font-bold text-gray-500">{index + 1} / {cards.length}</span>
        <div className="flex gap-2">
            <span className={`px-2 py-0.5 rounded text-xs font-bold ${IMPORTANCE_STYLE[card.importance] || IMPORTANCE_STYLE.B}`}>{card.importance}</span>
            <span className="px-2 py-0.5 rounded text-xs font-bold bg-blue-100 text-blue-700">{card.subject}</span>
        </div>
      </div>

      {/* Scrollable Content */}
      <div className="flex-1 overflow-y-auto p-4 space-y-6 pb-32">
        {/* Outdated Warning */}
        {card.is_outdated && (
          <div className="bg-orange-50 border-l-4 border-orange-500 p-4 rounded shadow-sm">
            <div className="flex">
              <div className="flex-shrink-0">⚠️</div>
              <div className="ml-3">
                <p className="text-sm text-orange-700 font-bold">Outdated Law Warning</p>
                <p className="text-xs text-orange-600 mt-1">This card may reflect outdated statutes or precedents.</p>
              </div>
            </div>
          </div>
        )}

        {/* Front: Question */}
        <div className="bg-white p-6 rounded-2xl shadow-sm border border-gray-100">
            <div className="mb-2 text-xs font-bold text-gray-400 uppercase tracking-wider">Statement</div>
            <div className="text-lg font-medium text-gray-900 leading-relaxed">
                {card.statement}
            </div>
        </div>

        {/* Back: Answer & Explanation */}
        {revealed && (
            <div className="space-y-4 animate-in fade-in slide-in-from-bottom-4 duration-300">
                {/* Answer Verdict */}
                <div className={`p-4 rounded-xl text-center font-black text-3xl shadow-sm ${card.is_correct ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
                    {card.is_correct ? "O" : "X"}
                </div>

                {/* Union Textbook Style Explanation */}
                <div className="bg-white rounded-2xl shadow-sm border border-gray-100 overflow-hidden">
                    {/* Legal Basis (Blue) */}
                    {card.legal_basis && (
                        <div className="border-l-4 border-blue-500 bg-blue-50 p-3">
                            <span className="text-xs font-bold text-blue-600 block mb-1">LEGAL BASIS</span>
                            <span className="text-sm text-gray-800 font-medium">{card.legal_basis}</span>
                        </div>
                    )}
                    
                    {/* Case Citation (Purple) */}
                    {card.case_citation && (
                        <div className="border-l-4 border-purple-500 bg-purple-50 p-3">
                            <span className="text-xs font-bold text-purple-600 block mb-1">CASE CITATION</span>
                            <span className="text-sm text-gray-800 font-medium">{card.case_citation}</span>
                        </div>
                    )}

                    <div className="p-5">
                        {/* Explanation Core */}
                        <div className="mb-4">
                            <h4 className="text-xs font-bold text-gray-400 uppercase mb-1">Core Logic</h4>
                            <p className="text-sm font-semibold text-gray-900">{card.explanation_core || card.explanation}</p>
                        </div>
                        
                        {/* Keywords */}
                        {card.keywords && (
                            <div className="flex flex-wrap gap-2 mt-4">
                                {card.keywords.map((k, i) => (
                                    <span key={i} className="px-2 py-1 bg-gray-100 text-gray-600 text-xs rounded-full font-medium">#{k}</span>
                                ))}
                            </div>
                        )}
                    </div>
                </div>
            </div>
        )}
      </div>

      {/* FAB: AI Tutor */}
      <button 
        onClick={handleAiExplain}
        className="absolute bottom-24 right-6 w-14 h-14 bg-indigo-600 rounded-full shadow-lg flex items-center justify-center text-white hover:bg-indigo-700 transition-transform active:scale-95 z-20"
        title="Ask AI Tutor"
      >
        <span className="text-2xl">🤖</span>
      </button>

      {/* Bottom Action Bar */}
      <div className="absolute bottom-0 left-0 right-0 bg-white border-t border-gray-200 p-4 safe-area-bottom z-30">
        {!revealed ? (
            <button 
                onClick={() => setRevealed(true)}
                className="w-full bg-black text-white font-bold py-4 rounded-xl shadow-lg hover:bg-gray-800 active:scale-[0.98] transition-all"
            >
                Show Answer
            </button>
        ) : (
            <div className="grid grid-cols-4 gap-3">
                <button onClick={() => grade(1)} className="flex flex-col items-center justify-center p-2 rounded-xl bg-red-100 text-red-700 hover:bg-red-200 transition-colors">
                    <span className="font-bold text-sm">Again</span>
                    <span className="text-[10px] opacity-75">1m</span>
                </button>
                <button onClick={() => grade(2)} className="flex flex-col items-center justify-center p-2 rounded-xl bg-gray-200 text-gray-700 hover:bg-gray-300 transition-colors">
                    <span className="font-bold text-sm">Hard</span>
                    <span className="text-[10px] opacity-75">2d</span>
                </button>
                <button onClick={() => grade(3)} className="flex flex-col items-center justify-center p-2 rounded-xl bg-green-100 text-green-700 hover:bg-green-200 transition-colors">
                    <span className="font-bold text-sm">Good</span>
                    <span className="text-[10px] opacity-75">3d</span>
                </button>
                <button onClick={() => grade(4)} className="flex flex-col items-center justify-center p-2 rounded-xl bg-blue-100 text-blue-700 hover:bg-blue-200 transition-colors">
                    <span className="font-bold text-sm">Easy</span>
                    <span className="text-[10px] opacity-75">4d</span>
                </button>
            </div>
        )}
      </div>
    </div>
  );
}

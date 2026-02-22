import { Suspense } from "react";
import StudySession from "@/components/StudySession";

export default function StudyPage() {
  return (
    <Suspense
      fallback={
        <div className="flex h-screen items-center justify-center bg-gray-50">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-blue-500 border-t-transparent" />
        </div>
      }
    >
      <StudySession />
    </Suspense>
  );
}

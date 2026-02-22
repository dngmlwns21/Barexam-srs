import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "변호사시험 SRS",
  description: "Korean Bar Exam Spaced Repetition System",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ko">
      <body className="antialiased">{children}</body>
    </html>
  );
}

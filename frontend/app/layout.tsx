import type { Metadata } from "next";
// Fonts are self-hosted from npm (@fontsource) instead of next/font/google, so
// `npm run build` (incl. the Docker build) never needs to reach Google Fonts.
import "@fontsource-variable/inter";
import "@fontsource-variable/space-grotesk";
import "@fontsource/ibm-plex-mono/400.css";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Football Match Video Analysis",
  description: "AI-powered football match video analysis platform",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className="h-full antialiased"
    >
      <body className="min-h-full flex flex-col bg-slate-950 text-slate-100">
        <header className="border-b border-slate-800 bg-slate-900/80 shadow-sm">
          <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
            <Link href="/" className="flex items-center gap-3 font-semibold tracking-tight">
              <span className="flex h-8 w-8 items-center justify-center rounded-md bg-amber-500 text-lg text-slate-950">⚽</span>
              <span className="font-display">Match Analysis</span>
            </Link>
            <nav className="flex items-center gap-4 text-sm font-display">
              <Link href="/" className="text-slate-300 hover:text-white">
                Matches
              </Link>
              <Link
                href="/matches/new"
                className="rounded-md bg-emerald-600 px-3 py-1.5 font-medium text-white hover:bg-emerald-500"
              >
                New Analysis
              </Link>
            </nav>
          </div>
        </header>
        <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-8">{children}</main>
      </body>
    </html>
  );
}

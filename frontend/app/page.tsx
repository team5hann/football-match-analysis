"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, ApiError, type Match } from "@/lib/api";
import { formatDate } from "@/lib/format";
import StatusBadge from "@/components/StatusBadge";
import DeleteMatchButton from "@/components/DeleteMatchButton";

export default function HomePage() {
  const [matches, setMatches] = useState<Match[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listMatches()
      .then(setMatches)
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : "Failed to load matches");
      });
  }, []);

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Matches</h1>
        <Link
          href="/matches/new"
          className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500"
        >
          + New Analysis
        </Link>
      </div>

      {error && (
        <div className="mb-4 rounded-md border border-red-800 bg-red-950 px-4 py-3 text-sm text-red-200">
          {error}. Is the backend running at{" "}
          {process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}?
        </div>
      )}

      {matches === null && !error && (
        <p className="text-slate-400">Loading matches…</p>
      )}

      {matches !== null && matches.length === 0 && (
        <div className="rounded-lg border border-dashed border-slate-700 bg-slate-900/40 px-6 py-16 text-center">
          <p className="mb-4 text-slate-400">No matches yet.</p>
          <Link
            href="/matches/new"
            className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500"
          >
            Upload your first match
          </Link>
        </div>
      )}

      {matches && matches.length > 0 && (
        <ul className="divide-y divide-slate-800 rounded-lg border border-slate-800 bg-slate-900/40">
          {matches.map((match) => (
            <li
              key={match.id}
              className="flex items-center justify-between gap-4 px-5 py-4 hover:bg-slate-800/50"
            >
              <Link
                href={`/matches/${match.id}`}
                className="flex flex-1 items-center justify-between gap-4"
              >
                <div>
                  <p className="font-medium text-slate-100">
                    {match.name || `Match #${match.id}`}
                  </p>
                  <p className="text-sm text-slate-400">
                    {match.competition || "No competition set"} · {formatDate(match.match_date)}
                  </p>
                </div>
                <StatusBadge status={match.status} />
              </Link>
              <DeleteMatchButton
                matchId={match.id}
                matchLabel={match.name || `Match #${match.id}`}
                onDeleted={() =>
                  setMatches((prev) => prev?.filter((m) => m.id !== match.id) ?? prev)
                }
              />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

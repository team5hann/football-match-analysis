"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { api, ApiError, type Team, type TeamStats } from "@/lib/api";
import { formatDate } from "@/lib/format";

export default function TeamPage({ params }: PageProps<"/teams/[id]">) {
  const { id } = use(params);
  const teamId = Number(id);

  const [team, setTeam] = useState<Team | null>(null);
  const [stats, setStats] = useState<TeamStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getTeam(teamId).then(setTeam).catch(() => undefined);
    api
      .getTeamStats(teamId)
      .then(setStats)
      .catch((err: unknown) => setError(err instanceof ApiError ? err.message : "Failed to load team stats"));
  }, [teamId]);

  if (error) {
    return <p className="rounded-md border border-red-800 bg-red-950 px-4 py-3 text-sm text-red-200">{error}</p>;
  }
  if (!stats) {
    return <p className="text-slate-400">Loading team…</p>;
  }

  const pct = (value: number | null) => (value == null ? "—" : `${value.toFixed(1)}%`);

  return (
    <div className="space-y-6">
      <div>
        <Link href="/" className="text-sm text-slate-400 hover:text-slate-200">← Matches</Link>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight">{team?.name ?? stats.team_name}</h1>
        <p className="text-sm text-slate-400">Aggregated across {stats.matches_played} match{stats.matches_played === 1 ? "" : "es"}</p>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Matches" value={String(stats.matches_played)} />
        <Stat label="With analysis" value={String(stats.matches_with_analysis)} />
        <Stat label="Avg possession" value={pct(stats.avg_possession_pct)} />
        <Stat label="Total / avg xG" value={`${stats.total_xg.toFixed(2)} / ${stats.avg_xg.toFixed(2)}`} />
        <Stat label="Total shots" value={String(stats.total_shots)} />
        <Stat
          label="W / D / L"
          value={stats.results_recorded ? `${stats.wins} / ${stats.draws} / ${stats.losses}` : "—"}
        />
        <Stat
          label="Goals for / against"
          value={stats.results_recorded ? `${stats.goals_for} / ${stats.goals_against}` : "—"}
        />
      </div>

      <section className="rounded-lg border border-slate-800 bg-slate-900/40 p-5">
        <h2 className="font-medium text-slate-200">Match by match</h2>
        {stats.trend.length === 0 ? (
          <p className="mt-3 text-sm text-slate-500">No matches for this team yet.</p>
        ) : (
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="pb-2 font-medium">Date</th>
                  <th className="pb-2 font-medium">Opponent</th>
                  <th className="pb-2 font-medium">H/A</th>
                  <th className="pb-2 font-medium">Score</th>
                  <th className="pb-2 font-medium">Result</th>
                  <th className="pb-2 font-medium">Possession</th>
                  <th className="pb-2 font-medium">Shots (xG)</th>
                  <th className="pb-2 font-medium"></th>
                </tr>
              </thead>
              <tbody>
                {stats.trend.map((row) => (
                  <tr key={row.match_id} className="border-t border-slate-800 text-slate-300">
                    <td className="py-2">{row.match_date ? formatDate(row.match_date) : "—"}</td>
                    <td className="py-2">{row.opponent_name ?? "—"}</td>
                    <td className="py-2 uppercase">{row.side === "home" ? "H" : "A"}</td>
                    <td className="py-2">
                      {row.goals_for != null && row.goals_against != null
                        ? `${row.goals_for}–${row.goals_against}`
                        : "—"}
                    </td>
                    <td className="py-2">
                      <span
                        className={
                          row.result === "win"
                            ? "text-emerald-400"
                            : row.result === "loss"
                              ? "text-red-400"
                              : row.result === "draw"
                                ? "text-slate-300"
                                : "text-slate-600"
                        }
                      >
                        {row.result ? row.result.toUpperCase() : "—"}
                      </span>
                    </td>
                    <td className="py-2">{pct(row.possession_pct)}</td>
                    <td className="py-2">
                      {row.shots}
                      <span className="text-slate-500"> ({row.xg.toFixed(2)})</span>
                    </td>
                    <td className="py-2">
                      <Link href={`/matches/${row.match_id}`} className="text-sky-400 hover:text-sky-300">
                        detail →
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="rounded-lg border border-slate-800 bg-slate-900/40 p-5">
        <h2 className="font-medium text-slate-200">Players across matches</h2>
        <p className="mt-2 rounded-md border border-amber-800/60 bg-amber-950/30 px-3 py-2 text-xs text-amber-300">
          Grouped by shirt number for this team — a rough heuristic, not a real identity. {stats.player_identity_note}
          {stats.players_without_jersey_number > 0 &&
            ` ${stats.players_without_jersey_number} identities without a confident number were left out.`}
        </p>
        {stats.players.length === 0 ? (
          <p className="mt-3 text-sm text-slate-500">No numbered players aggregated yet.</p>
        ) : (
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="pb-2 font-medium">#</th>
                  <th className="pb-2 font-medium">Matches</th>
                  <th className="pb-2 font-medium">Touches (per match)</th>
                  <th className="pb-2 font-medium">Passes (cmp/total)</th>
                  <th className="pb-2 font-medium">Shots (xG)</th>
                  <th className="pb-2 font-medium">Duels (won/total)</th>
                  <th className="pb-2 font-medium">Dribbles (ok/total)</th>
                </tr>
              </thead>
              <tbody>
                {stats.players.map((p) => (
                  <tr key={p.jersey_number} className="border-t border-slate-800 text-slate-300">
                    <td className="py-2 font-medium text-slate-100">#{p.jersey_number}</td>
                    <td className="py-2">{p.matches_played}</td>
                    <td className="py-2">
                      {p.touches}
                      <span className="text-slate-500"> ({p.touches_per_match})</span>
                    </td>
                    <td className="py-2">
                      {p.passes_completed}/{p.passes_total}
                    </td>
                    <td className="py-2">
                      {p.shots}
                      <span className="text-slate-500"> ({p.xg.toFixed(2)})</span>
                    </td>
                    <td className="py-2">
                      {p.duels_won}/{p.duels_total}
                    </td>
                    <td className="py-2">
                      {p.dribbles_completed}/{p.dribbles_total}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-slate-800 bg-slate-950/60 px-3 py-2">
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-0.5 text-lg font-semibold text-slate-100">{value}</p>
    </div>
  );
}

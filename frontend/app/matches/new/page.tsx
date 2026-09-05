"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError, type Team } from "@/lib/api";

const NEW_TEAM_VALUE = "__new__";

export default function NewMatchPage() {
  const router = useRouter();
  const [teams, setTeams] = useState<Team[]>([]);

  const [name, setName] = useState("");
  const [competition, setCompetition] = useState("");
  const [matchDate, setMatchDate] = useState("");

  const [homeTeamId, setHomeTeamId] = useState<string>(NEW_TEAM_VALUE);
  const [homeTeamName, setHomeTeamName] = useState("");
  const [awayTeamId, setAwayTeamId] = useState<string>(NEW_TEAM_VALUE);
  const [awayTeamName, setAwayTeamName] = useState("");

  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listTeams().then(setTeams).catch(() => setTeams([]));
  }, []);

  async function resolveTeamId(selectedId: string, newName: string): Promise<number | undefined> {
    if (selectedId !== NEW_TEAM_VALUE) {
      return Number(selectedId);
    }
    if (!newName.trim()) return undefined;
    const team = await api.createTeam({ name: newName.trim() });
    return team.id;
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const homeId = await resolveTeamId(homeTeamId, homeTeamName);
      const awayId = await resolveTeamId(awayTeamId, awayTeamName);

      const match = await api.createMatch({
        name: name.trim() || undefined,
        competition: competition.trim() || undefined,
        match_date: matchDate ? new Date(matchDate).toISOString() : undefined,
        home_team_id: homeId,
        away_team_id: awayId,
      });

      if (file) {
        setProgress(0);
        await api.uploadVideo(match.id, file, setProgress);
      }

      router.push(`/matches/${match.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
      setSubmitting(false);
      setProgress(null);
    }
  }

  return (
    <div className="mx-auto max-w-2xl">
      <p className="mb-2 text-xs font-medium uppercase tracking-[0.18em] text-slate-500">Match setup</p>
      <h1 className="mb-8 text-3xl font-semibold tracking-tight">New Analysis</h1>

      <form onSubmit={handleSubmit} className="space-y-6">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <TeamField
            label="Home team"
            teams={teams}
            selectedId={homeTeamId}
            onSelect={setHomeTeamId}
            newName={homeTeamName}
            onNewName={setHomeTeamName}
          />
          <TeamField
            label="Away team"
            teams={teams}
            selectedId={awayTeamId}
            onSelect={setAwayTeamId}
            newName={awayTeamName}
            onNewName={setAwayTeamName}
          />
        </div>

        <Field label="Match name (optional)">
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Season opener"
            className="input"
          />
        </Field>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Competition">
            <input
              type="text"
              value={competition}
              onChange={(e) => setCompetition(e.target.value)}
              placeholder="e.g. Regional League"
              className="input"
            />
          </Field>
          <Field label="Date">
            <input
              type="date"
              value={matchDate}
              onChange={(e) => setMatchDate(e.target.value)}
              className="input"
            />
          </Field>
        </div>

        <Field label="Match video">
          <label
            htmlFor="video"
            className="flex cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed border-slate-700 bg-slate-900/40 px-6 py-10 text-center shadow-sm hover:border-emerald-600"
          >
            <span className="text-3xl">🎬</span>
            <span className="mt-2 text-sm text-slate-300">
              {file ? file.name : "Click to choose a video file"}
            </span>
            <span className="mt-1 text-xs text-slate-500">MP4, MOV, AVI, or MKV</span>
            <input
              id="video"
              type="file"
              accept=".mp4,.mov,.avi,.mkv,video/*"
              className="hidden"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </label>
        </Field>

        {progress !== null && (
          <div>
            <div className="mb-1 flex justify-between text-xs text-slate-400">
              <span>Uploading video…</span>
              <span>{progress}%</span>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-slate-800">
              <div
                className="h-full rounded-full bg-emerald-500 transition-all"
                style={{ width: `${progress}%` }}
              />
            </div>
          </div>
        )}

        {error && (
          <div className="rounded-md border border-red-800 bg-red-950 px-4 py-3 text-sm text-red-200">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded-md bg-emerald-600 px-4 py-2.5 font-medium text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {submitting ? "Creating match…" : "Create match"}
        </button>
      </form>

      <style jsx global>{`
        .input {
          width: 100%;
          border-radius: 0.4375rem;
          border: 1px solid var(--color-border);
          background-color: var(--color-surface-raised);
          padding: 0.625rem 0.75rem;
          color: var(--color-text);
        }
        .input:focus {
          outline: none;
          border-color: var(--color-home);
        }
      `}</style>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium text-slate-300">{label}</span>
      {children}
    </label>
  );
}

function TeamField({
  label,
  teams,
  selectedId,
  onSelect,
  newName,
  onNewName,
}: {
  label: string;
  teams: Team[];
  selectedId: string;
  onSelect: (id: string) => void;
  newName: string;
  onNewName: (name: string) => void;
}) {
  return (
    <Field label={label}>
      <select
        value={selectedId}
        onChange={(e) => onSelect(e.target.value)}
        className="input mb-2"
      >
        <option value={NEW_TEAM_VALUE}>+ New team</option>
        {teams.map((team) => (
          <option key={team.id} value={team.id}>
            {team.name}
          </option>
        ))}
      </select>
      {selectedId === NEW_TEAM_VALUE && (
        <input
          type="text"
          value={newName}
          onChange={(e) => onNewName(e.target.value)}
          placeholder="Team name"
          className="input"
        />
      )}
    </Field>
  );
}

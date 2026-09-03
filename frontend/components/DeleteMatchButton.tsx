"use client";

import { useState } from "react";
import { api, ApiError } from "@/lib/api";

export default function DeleteMatchButton({
  matchId,
  matchLabel,
  onDeleted,
  className = "",
}: {
  matchId: number;
  matchLabel: string;
  onDeleted: () => void;
  className?: string;
}) {
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleDelete(event: React.MouseEvent) {
    event.preventDefault();
    event.stopPropagation();

    const confirmed = window.confirm(
      `Delete "${matchLabel}"? This also deletes its uploaded video. This cannot be undone.`
    );
    if (!confirmed) return;

    setDeleting(true);
    setError(null);
    try {
      await api.deleteMatch(matchId);
      onDeleted();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to delete match");
      setDeleting(false);
    }
  }

  return (
    <div className={className}>
      <button
        type="button"
        onClick={handleDelete}
        disabled={deleting}
        className="rounded-md border border-red-800 px-3 py-1.5 text-xs font-medium text-red-300 hover:bg-red-950 disabled:cursor-not-allowed disabled:opacity-60"
      >
        {deleting ? "Deleting…" : "Delete"}
      </button>
      {error && <p className="mt-1 text-xs text-red-400">{error}</p>}
    </div>
  );
}

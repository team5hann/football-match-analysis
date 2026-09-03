"use client";

import { useState } from "react";
import { api, ApiError } from "@/lib/api";

export default function VideoUploadPanel({
  matchId,
  onUploaded,
}: {
  matchId: number;
  onUploaded: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleUpload() {
    if (!file) return;
    setError(null);
    setProgress(0);
    try {
      await api.uploadVideo(matchId, file, setProgress);
      onUploaded();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Upload failed");
      setProgress(null);
    }
  }

  return (
    <div className="rounded-lg border border-dashed border-slate-700 bg-slate-900/40 p-8 text-center">
      <p className="mb-4 text-slate-300">No video uploaded for this match yet.</p>

      <label
        htmlFor="video-upload"
        className="mx-auto flex max-w-sm cursor-pointer flex-col items-center justify-center rounded-lg border border-slate-700 bg-slate-950/60 px-6 py-8 hover:border-emerald-600"
      >
        <span className="text-3xl">🎬</span>
        <span className="mt-2 text-sm text-slate-300">
          {file ? file.name : "Click to choose a video file"}
        </span>
        <span className="mt-1 text-xs text-slate-500">MP4, MOV, AVI, or MKV</span>
        <input
          id="video-upload"
          type="file"
          accept=".mp4,.mov,.avi,.mkv,video/*"
          className="hidden"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
      </label>

      {progress !== null && (
        <div className="mx-auto mt-4 max-w-sm">
          <div className="mb-1 flex justify-between text-xs text-slate-400">
            <span>Uploading…</span>
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
        <div className="mx-auto mt-4 max-w-sm rounded-md border border-red-800 bg-red-950 px-4 py-3 text-sm text-red-200">
          {error}
        </div>
      )}

      <button
        type="button"
        onClick={handleUpload}
        disabled={!file || progress !== null}
        className="mt-5 rounded-md bg-emerald-600 px-4 py-2 font-medium text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-60"
      >
        Upload video
      </button>
    </div>
  );
}

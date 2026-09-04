"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, ApiError, mediaUrl, type MatchDetail } from "@/lib/api";
import { formatBytes, formatDate, formatDuration, resolutionLabel } from "@/lib/format";
import StatusBadge from "@/components/StatusBadge";
import VideoUploadPanel from "@/components/VideoUploadPanel";
import DeleteMatchButton from "@/components/DeleteMatchButton";

export default function MatchDetailPage({ params }: PageProps<"/matches/[id]">) {
  const { id } = use(params);
  const matchId = Number(id);
  const router = useRouter();

  const [match, setMatch] = useState<MatchDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [detection, setDetection] = useState<Awaited<ReturnType<typeof api.getDetectionStatus>> | null>(null);
  const [startingDetection, setStartingDetection] = useState(false);
  const [detectionError, setDetectionError] = useState<string | null>(null);

  const reload = useCallback(() => {
    api
      .getMatch(matchId)
      .then(setMatch)
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : "Failed to load match");
      });
  }, [matchId]);

  useEffect(() => {
    reload();
  }, [reload]);

  const videoId = match?.videos[0]?.id;
  useEffect(() => {
    if (!videoId) return;
    let active = true;

    const loadDetection = () => {
      api
        .getDetectionStatus(videoId)
        .then((status) => {
          if (active) setDetection(status);
        })
        .catch(() => {
          if (active) setDetectionError("Failed to load detection status");
        });
    };

    loadDetection();
    const interval = window.setInterval(loadDetection, 2000);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [videoId]);

  async function handleStartDetection() {
    if (!video) return;
    setStartingDetection(true);
    setDetectionError(null);
    try {
      setDetection(await api.startDetection(video.id));
    } catch (err: unknown) {
      setDetectionError(err instanceof ApiError ? err.message : "Failed to start detection");
    } finally {
      setStartingDetection(false);
    }
  }

  if (error) {
    return (
      <div className="rounded-md border border-red-800 bg-red-950 px-4 py-3 text-sm text-red-200">
        {error}
      </div>
    );
  }

  if (!match) {
    return <p className="text-slate-400">Loading match…</p>;
  }

  const video = match.videos[0];

  return (
    <div>
      <Link href="/" className="text-sm text-slate-400 hover:text-white">
        ← Back to matches
      </Link>

      <div className="mt-3 mb-6 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">
            {match.home_team?.name ?? "Home"}{" "}
            <span className="text-slate-500">vs</span> {match.away_team?.name ?? "Away"}
          </h1>
          <p className="text-sm text-slate-400">
            {match.name ? `${match.name} · ` : ""}
            {match.competition || "No competition set"} · {formatDate(match.match_date)}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <StatusBadge status={match.status} />
          <DeleteMatchButton
            matchId={match.id}
            matchLabel={match.name || `Match #${match.id}`}
            onDeleted={() => router.push("/")}
          />
        </div>
      </div>

      {video ? (
        <div className="space-y-6">
          <div className="overflow-hidden rounded-lg border border-slate-800 bg-black">
            <video
              controls
              className="aspect-video w-full"
              src={mediaUrl(video.stream_url)}
              preload="metadata"
            >
              Your browser does not support the video tag.
            </video>
          </div>

          {video.status === "failed" && (
            <div className="rounded-md border border-red-800 bg-red-950 px-4 py-3 text-sm text-red-200">
              Metadata extraction failed: {video.error_message}
            </div>
          )}

          <section className="rounded-lg border border-slate-800 bg-slate-900/40 p-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="font-semibold text-slate-100">Player detection</h2>
                <p className="mt-1 text-sm text-slate-400">
                  {detection?.status === "processing"
                    ? "Analyzing one frame per second…"
                    : detection?.status === "analyzed"
                      ? `${detection.detections_count} detections found`
                      : "Run YOLOv8 on this video"}
                </p>
              </div>
              <button
                type="button"
                onClick={handleStartDetection}
                disabled={startingDetection || detection?.status === "processing"}
                className="rounded-md bg-emerald-500 px-3 py-2 text-sm font-semibold text-slate-950 hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {startingDetection || detection?.status === "processing" ? "Detecting…" : "Spustit detekci"}
              </button>
            </div>

            {detectionError && <p className="mt-3 text-sm text-red-300">{detectionError}</p>}
            {detection?.status === "analyzed" && detection.detections_count > 0 && (
              <div className="mt-5 overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="text-xs uppercase tracking-wide text-slate-500">
                    <tr>
                      <th className="pb-2 font-medium">Time</th>
                      <th className="pb-2 font-medium">Players</th>
                      <th className="pb-2 font-medium">Balls</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...new Set(detection.detections.map((item) => item.frame_timestamp))].map((timestamp) => {
                      const frameDetections = detection.detections.filter(
                        (item) => item.frame_timestamp === timestamp
                      );
                      return (
                        <tr key={timestamp} className="border-t border-slate-800 text-slate-300">
                          <td className="py-2">{timestamp}s</td>
                          <td className="py-2">{frameDetections.filter((item) => item.class === "player").length}</td>
                          <td className="py-2">{frameDetections.filter((item) => item.class === "ball").length}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <div className="grid grid-cols-2 gap-4 rounded-lg border border-slate-800 bg-slate-900/40 p-5 sm:grid-cols-4">
            <Stat label="Duration" value={formatDuration(video.duration_seconds)} />
            <Stat label="Resolution" value={resolutionLabel(video.width, video.height)} />
            <Stat label="FPS" value={video.fps ? video.fps.toFixed(1) : "—"} />
            <Stat label="File size" value={formatBytes(video.file_size_bytes)} />
            <Stat label="Video codec" value={video.video_codec ?? "—"} />
            <Stat label="Audio codec" value={video.audio_codec ?? "—"} />
            <Stat label="Uploaded" value={formatDate(video.uploaded_at)} />
            <Stat label="Original filename" value={video.original_filename} />
          </div>

          <div className="rounded-lg border border-dashed border-slate-700 bg-slate-900/40 p-5 text-sm text-slate-400">
            Statistics, events, and tactical analysis will appear here once AI processing
            (Phase 2+) is implemented.
          </div>
        </div>
      ) : (
        <VideoUploadPanel matchId={match.id} onUploaded={reload} />
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 font-medium text-slate-100">{value}</p>
    </div>
  );
}

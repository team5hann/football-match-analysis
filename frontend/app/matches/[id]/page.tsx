"use client";

import { use, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, ApiError, mediaUrl, type MatchDetail, type PlayerOption, type TeamClusterAssignment, type TeamClusterRole } from "@/lib/api";
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
  const [startingEnrichment, setStartingEnrichment] = useState(false);
  const [clusterAssignments, setClusterAssignments] = useState<TeamClusterAssignment[]>([]);
  const [savingClusters, setSavingClusters] = useState(false);
  const [analysis, setAnalysis] = useState<Awaited<ReturnType<typeof api.getAnalysis>> | null>(null);
  const [startingAnalysis, setStartingAnalysis] = useState(false);
  const videoElement = useRef<HTMLVideoElement>(null);
  const [heatmapMode, setHeatmapMode] = useState<"team" | "player">("team");
  const [heatmapTeam, setHeatmapTeam] = useState<"home" | "away">("home");
  const [heatmapTrack, setHeatmapTrack] = useState<number | null>(null);
  const [heatmap, setHeatmap] = useState<Awaited<ReturnType<typeof api.getHeatmap>> | null>(null);
  const [heatmapError, setHeatmapError] = useState<string | null>(null);
  const [playerOptions, setPlayerOptions] = useState<PlayerOption[]>([]);
  const [passingNetwork, setPassingNetwork] = useState<Awaited<ReturnType<typeof api.getPassingNetwork>> | null>(null);
  const [passingTeam, setPassingTeam] = useState<"home" | "away">("home");
  const [tacticalTeam, setTacticalTeam] = useState<"home" | "away" | "both">("home");
  const [tactical, setTactical] = useState<Awaited<ReturnType<typeof api.getTactical>> | null>(null);
  const [tacticalAway, setTacticalAway] = useState<Awaited<ReturnType<typeof api.getTactical>> | null>(null);
  const [shots, setShots] = useState<Awaited<ReturnType<typeof api.getShots>> | null>(null);
  const [startingShots, setStartingShots] = useState(false);

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

  useEffect(() => {
    api.getTeamClusters(matchId).then(setClusterAssignments).catch(() => undefined);
    api.getDetectedPlayers(matchId).then(setPlayerOptions).catch(() => undefined);
  }, [matchId]);

  useEffect(() => {
    api.getPassingNetwork(matchId).then(setPassingNetwork).catch(() => undefined);
  }, [matchId]);

  useEffect(() => {
    api.getShots(matchId).then(setShots).catch(() => undefined);
  }, [matchId]);

  useEffect(() => {
    const teams = tacticalTeam === "both" ? ["home", "away"] as const : [tacticalTeam];
    Promise.all(teams.map((team) => api.getTactical(matchId, team)))
      .then((results) => {
        if (tacticalTeam === "both") {
          setTactical(results[0]);
          setTacticalAway(results[1]);
        } else {
          setTactical(results[0]);
          setTacticalAway(null);
        }
      })
      .catch(() => {
        setTactical(null);
        setTacticalAway(null);
      });
  }, [matchId, tacticalTeam]);

  useEffect(() => {
    if (heatmapMode === "team") {
      const cluster = clusterAssignments.find((item) => item.role === heatmapTeam)?.cluster_id;
      if (cluster === undefined) {
        return;
      }
      api.getHeatmap(matchId, { mode: "team", team_color_cluster: cluster })
        .then(setHeatmap)
        .catch(() => setHeatmapError("No heatmap data available for this team"));
      return;
    }
    if (heatmapTrack === null) {
      return;
    }
    api.getHeatmap(matchId, { mode: "player", track_id: heatmapTrack })
      .then(setHeatmap)
      .catch(() => setHeatmapError("No heatmap data available for this player"));
  }, [clusterAssignments, heatmapMode, heatmapTeam, heatmapTrack, matchId]);

  useEffect(() => {
    let active = true;
    const loadAnalysis = () => {
      api.getAnalysis(matchId).then((result) => {
        if (active) setAnalysis(result);
      }).catch(() => undefined);
    };
    loadAnalysis();
    const interval = window.setInterval(loadAnalysis, 2000);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [matchId]);

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

  async function handleStartEnrichment() {
    if (!video) return;
    setStartingEnrichment(true);
    setDetectionError(null);
    try {
      setDetection(await api.startEnrichment(video.id));
    } catch (err: unknown) {
      setDetectionError(err instanceof ApiError ? err.message : "Failed to start color and OCR processing");
    } finally {
      setStartingEnrichment(false);
    }
  }

  async function handleStartAnalysis() {
    setStartingAnalysis(true);
    setDetectionError(null);
    try {
      setAnalysis(await api.startAnalysis(matchId));
    } catch (err: unknown) {
      setDetectionError(err instanceof ApiError ? err.message : "Failed to start analysis");
    } finally {
      setStartingAnalysis(false);
    }
  }

  async function handleStartShots() {
    setStartingShots(true);
    setDetectionError(null);
    try {
      setShots(await api.startShotDetection(matchId));
    } catch (err: unknown) {
      setDetectionError(err instanceof ApiError ? err.message : "Failed to detect shots");
    } finally {
      setStartingShots(false);
    }
  }

  function setClusterRole(clusterId: number, role: "" | TeamClusterRole) {
    setClusterAssignments((current) => {
      if (!role) return current.filter((item) => item.cluster_id !== clusterId);
      const existing = current.find((item) => item.cluster_id === clusterId);
      const assignment = existing ?? { id: 0, cluster_id: clusterId, role, team_id: null, detections_count: 0 };
      return [...current.filter((item) => item.cluster_id !== clusterId), { ...assignment, role }];
    });
  }

  async function saveClusters() {
    setSavingClusters(true);
    try {
      setClusterAssignments(
        await api.saveTeamClusters(
          matchId,
          clusterAssignments.map(({ cluster_id, role }) => ({
            cluster_id,
            role,
            team_id: role === "home" ? match?.home_team_id ?? null : role === "away" ? match?.away_team_id ?? null : null,
          }))
        )
      );
    } catch (err: unknown) {
      setDetectionError(err instanceof ApiError ? err.message : "Failed to save team assignments");
    } finally {
      setSavingClusters(false);
    }
  }

  async function updateJerseyNumber(detectionId: number, value: string) {
    const jerseyNumber = value.trim() === "" ? null : Number(value);
    if (jerseyNumber !== null && (!Number.isInteger(jerseyNumber) || jerseyNumber < 0 || jerseyNumber > 99)) return;
    try {
      const updated = await api.updateDetection(detectionId, jerseyNumber);
      setDetection((current) =>
        current ? { ...current, detections: current.detections.map((item) => item.id === updated.id ? updated : item) } : current
      );
    } catch (err: unknown) {
      setDetectionError(err instanceof ApiError ? err.message : "Failed to update jersey number");
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
              ref={videoElement}
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
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={handleStartDetection}
                  disabled={startingDetection || detection?.status === "processing"}
                  className="rounded-md bg-emerald-500 px-3 py-2 text-sm font-semibold text-slate-950 hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {startingDetection || detection?.status === "processing" ? "Detecting…" : "Spustit detekci"}
                </button>
                <button
                  type="button"
                  onClick={handleStartEnrichment}
                  disabled={startingEnrichment || detection?.status !== "analyzed"}
                  className="rounded-md border border-emerald-700 px-3 py-2 text-sm font-semibold text-emerald-300 hover:bg-emerald-950 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {startingEnrichment ? "Reading…" : "Barvy + OCR"}
                </button>
              </div>
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

            {detection?.status === "analyzed" && detection.detections.some((item) => item.team_color_cluster !== null) && (
              <div className="mt-5 border-t border-slate-800 pt-5">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <h3 className="font-medium text-slate-200">Team color clusters</h3>
                  <button
                    type="button"
                    onClick={saveClusters}
                    disabled={savingClusters}
                    className="rounded-md border border-slate-600 px-3 py-1.5 text-sm text-slate-200 hover:bg-slate-800 disabled:opacity-60"
                  >
                    {savingClusters ? "Saving…" : "Save assignments"}
                  </button>
                </div>
                <div className="mt-3 space-y-2">
                  {[...new Set(detection.detections.map((item) => item.team_color_cluster).filter((cluster): cluster is number => cluster !== null))]
                    .sort()
                    .map((cluster) => {
                      const assignment = clusterAssignments.find((item) => item.cluster_id === cluster);
                      const count = detection.detections.filter(
                        (item) => item.class === "player" && item.team_color_cluster === cluster
                      ).length;
                      return (
                        <label key={cluster} className="flex items-center justify-between gap-3 text-sm text-slate-300">
                          <span>Cluster {cluster} ({count} players)</span>
                          <select
                            value={assignment?.role ?? ""}
                            onChange={(event) => setClusterRole(cluster, event.target.value as "" | TeamClusterRole)}
                            className="rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-slate-200"
                          >
                            <option value="">Unassigned</option>
                            <option value="home">Home</option>
                            <option value="away">Away</option>
                            <option value="referee">Referee</option>
                          </select>
                        </label>
                      );
                    })}
                </div>
              </div>
            )}

            {detection?.status === "analyzed" && detection.detections.some((item) => item.jersey_number !== null) && (
              <div className="mt-5 border-t border-slate-800 pt-5">
                <h3 className="font-medium text-slate-200">Recognized jersey numbers</h3>
                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                  {detection.detections.filter((item) => item.class === "player" && item.jersey_number !== null).map((item) => (
                    <label key={item.id} className="flex items-center justify-between gap-3 text-sm text-slate-400">
                      <span>{item.frame_timestamp}s · cluster {item.team_color_cluster ?? "—"}</span>
                      <input
                        type="number"
                        min="0"
                        max="99"
                        defaultValue={item.jersey_number ?? ""}
                        onBlur={(event) => updateJerseyNumber(item.id, event.target.value)}
                        className="w-16 rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-center text-slate-100"
                      />
                    </label>
                  ))}
                </div>
              </div>
            )}
          </section>

          <section className="rounded-lg border border-slate-800 bg-slate-900/40 p-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="font-semibold text-slate-100">Shots &amp; xG</h2>
                <p className="mt-1 text-sm text-slate-400">Heuristic estimates from stored ball detections</p>
              </div>
              <button
                type="button"
                onClick={handleStartShots}
                disabled={startingShots || detection?.status !== "analyzed"}
                className="rounded-md bg-amber-400 px-3 py-2 text-sm font-semibold text-slate-950 hover:bg-amber-300 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {startingShots ? "Detecting…" : "Detect shots"}
              </button>
            </div>
            {shots?.shots.length ? (
              <>
                <div className="mt-5 grid grid-cols-2 gap-3">
                  <Stat label="Home xG" value={shots.home_xg.toFixed(2)} />
                  <Stat label="Away xG" value={shots.away_xg.toFixed(2)} />
                </div>
                <div className="mt-5 space-y-2">
                  {shots.shots.map((shot) => (
                    <button
                      key={shot.id}
                      type="button"
                      onClick={() => {
                        if (videoElement.current) {
                          videoElement.current.currentTime = shot.timestamp_seconds;
                          void videoElement.current.play();
                        }
                      }}
                      className="flex w-full items-center justify-between rounded-md border border-slate-800 px-3 py-2 text-left text-sm text-slate-300 hover:bg-slate-800"
                    >
                      <span>{shot.team_role === "home" ? "Home" : "Away"} · track {shot.track_id ?? "—"}</span>
                      <span className="text-slate-400">{shot.timestamp_seconds}s · xG {shot.xg.toFixed(2)}</span>
                    </button>
                  ))}
                </div>
              </>
            ) : (
              <p className="mt-5 text-sm text-slate-500">No shots detected yet. Sparse ball detections may miss short shooting sequences.</p>
            )}
            {shots?.note && <p className="mt-3 text-xs text-slate-500">{shots.note}</p>}
          </section>

          <section className="rounded-lg border border-slate-800 bg-slate-900/40 p-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="font-semibold text-slate-100">Tactical analysis</h2>
                <p className="mt-1 text-sm text-slate-400">Approximate whole-match formation and team shape</p>
              </div>
              <select
                value={tacticalTeam}
                onChange={(event) => setTacticalTeam(event.target.value as "home" | "away" | "both")}
                className="rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm text-slate-200"
              >
                <option value="home">Home</option>
                <option value="away">Away</option>
                <option value="both">Both teams</option>
              </select>
            </div>
            {tactical && tactical.players.length > 0 && (tacticalTeam !== "both" || (tacticalAway && tacticalAway.players.length > 0)) ? (
              <div className="mt-5">
                {tacticalTeam === "both" && tacticalAway ? (
                  <>
                    <div className="mb-3 flex flex-wrap gap-4 text-sm text-slate-300">
                      <span>Home <strong className="text-emerald-300">{tactical.formation}</strong></span>
                      <span>Away <strong className="text-sky-300">{tacticalAway.formation}</strong></span>
                    </div>
                    <TacticalPitch home={tactical} away={tacticalAway} />
                  </>
                ) : (
                  <>
                    <div className="grid gap-4 sm:grid-cols-4">
                      <Stat label="Formation" value={tactical.formation} />
                      <Stat label="Width" value={tactical.width.toFixed(3)} />
                      <Stat label="Depth" value={tactical.depth.toFixed(3)} />
                      <Stat label="Compactness" value={tactical.compactness.toFixed(3)} />
                    </div>
                    <TacticalPitch team={tacticalTeam} players={tactical.players} />
                  </>
                )}
                <p className="mt-3 text-xs text-slate-500">{tactical.coordinate_note}</p>
              </div>
            ) : (
              <p className="mt-5 text-sm text-slate-500">Run detection and analysis with team assignments to view tactical data.</p>
            )}
          </section>

          <section className="rounded-lg border border-slate-800 bg-slate-900/40 p-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="font-semibold text-slate-100">Passing network</h2>
                <p className="mt-1 text-sm text-slate-400">Passes estimated from stored possession events</p>
              </div>
              <select
                value={passingTeam}
                onChange={(event) => setPassingTeam(event.target.value as "home" | "away")}
                className="rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm text-slate-200"
              >
                <option value="home">Home</option>
                <option value="away">Away</option>
              </select>
            </div>
            {passingNetwork && passingNetwork[passingTeam].edges.length === 0 ? (
              <p className="mt-5 text-sm text-slate-500">Not enough pass data for this team yet.</p>
            ) : passingNetwork ? (
              <div className="mt-5">
                <svg viewBox="0 0 100 100" className="aspect-[5/3] w-full max-w-3xl border border-slate-600 bg-slate-950" role="img" aria-label={`${passingTeam} passing network`}>
                  {passingNetwork[passingTeam].edges.map((edge) => (
                    <line
                      key={`${edge.source_track_id}-${edge.target_track_id}`}
                      x1={edge.source_x * 100}
                      y1={edge.source_y * 100}
                      x2={edge.target_x * 100}
                      y2={edge.target_y * 100}
                      stroke="rgb(56 189 248)"
                      strokeOpacity={Math.min(0.9, 0.35 + edge.pass_count * 0.15)}
                      strokeWidth={0.5 + edge.pass_count * 0.45}
                    />
                  ))}
                  {passingNetwork[passingTeam].nodes.map((node) => (
                    <g key={node.track_id}>
                      <circle cx={node.average_x * 100} cy={node.average_y * 100} r="3" fill="rgb(16 185 129)" stroke="white" strokeWidth="0.5" />
                      <text x={node.average_x * 100} y={node.average_y * 100 + 1} textAnchor="middle" fontSize="3" fill="white">{node.jersey_number ?? `?${node.track_id}`}</text>
                    </g>
                  ))}
                </svg>
                <p className="mt-3 text-xs text-slate-500">{passingNetwork.coordinate_note}</p>
              </div>
            ) : (
              <p className="mt-5 text-sm text-slate-500">No passing data available yet. Run match analysis first.</p>
            )}
          </section>

          <section className="rounded-lg border border-slate-800 bg-slate-900/40 p-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="font-semibold text-slate-100">Heatmaps</h2>
                <p className="mt-1 text-sm text-slate-400">Movement occupancy from stored player detections</p>
              </div>
              <div className="flex flex-wrap gap-2">
                <select
                  value={heatmapMode}
                  onChange={(event) => setHeatmapMode(event.target.value as "team" | "player")}
                  className="rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm text-slate-200"
                >
                  <option value="team">Team</option>
                  <option value="player">Player</option>
                </select>
                {heatmapMode === "team" ? (
                  <select
                    value={heatmapTeam}
                    onChange={(event) => setHeatmapTeam(event.target.value as "home" | "away")}
                    className="rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm text-slate-200"
                  >
                    <option value="home">Home</option>
                    <option value="away">Away</option>
                  </select>
                ) : (
                  <select
                    value={heatmapTrack ?? ""}
                    onChange={(event) => setHeatmapTrack(event.target.value ? Number(event.target.value) : null)}
                    className="rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm text-slate-200"
                  >
                    <option value="">Select track</option>
                    {(["home", "away", "referee", "unknown"] as const).map((role) => {
                      const players = playerOptions.filter((player) => player.team_role === role);
                      if (players.length === 0) return null;
                      return (
                        <optgroup key={role} label={role[0].toUpperCase() + role.slice(1)}>
                          {players.map((player) => (
                            <option key={player.track_id} value={player.track_id}>
                              {player.jersey_number !== null ? `#${player.jersey_number}` : `Unknown #${player.track_id}`}
                            </option>
                          ))}
                        </optgroup>
                      );
                    })}
                  </select>
                )}
              </div>
            </div>
            {heatmapError && <p className="mt-3 text-sm text-amber-300">{heatmapError}</p>}
            {heatmap && (heatmapMode === "team"
              ? heatmapTeam === (clusterAssignments.find((item) => item.cluster_id === heatmap.team_color_cluster)?.role ?? "")
              : heatmapTrack === heatmap.track_id) ? (
              <div className="mt-5">
                <div
                  className="grid aspect-[5/3] w-full max-w-3xl overflow-hidden border border-slate-600 bg-slate-950"
                  style={{ gridTemplateColumns: `repeat(${heatmap.grid_width}, minmax(0, 1fr))`, gridTemplateRows: `repeat(${heatmap.grid_height}, minmax(0, 1fr))` }}
                >
                  {heatmap.grid.flatMap((row, rowIndex) => row.map((value, columnIndex) => {
                    const maxValue = Math.max(...heatmap.grid.flat(), 1);
                    const intensity = value / maxValue;
                    return (
                      <div
                        key={`${rowIndex}-${columnIndex}`}
                        title={`${value} observations`}
                        className="border border-slate-900/40"
                        style={{ backgroundColor: `rgba(16, 185, 129, ${0.08 + intensity * 0.92})` }}
                      />
                    );
                  }))}
                </div>
                <p className="mt-3 text-xs text-slate-500">{heatmap.total_observations} observations · {heatmap.coordinate_note}</p>
              </div>
            ) : (
              <p className="mt-5 text-sm text-slate-500">Run analysis and assign Home/Away clusters to view this heatmap.</p>
            )}
          </section>

          <section className="rounded-lg border border-slate-800 bg-slate-900/40 p-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="font-semibold text-slate-100">Match analysis</h2>
                <p className="mt-1 text-sm text-slate-400">
                  {analysis?.status === "processing" ? "Calculating from stored detections…" : "Possession, touches and movement estimates"}
                </p>
              </div>
              <button
                type="button"
                onClick={handleStartAnalysis}
                disabled={startingAnalysis || detection?.status !== "analyzed" || analysis?.status === "processing"}
                className="rounded-md bg-sky-500 px-3 py-2 text-sm font-semibold text-slate-950 hover:bg-sky-400 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {startingAnalysis || analysis?.status === "processing" ? "Analyzing…" : "Analyze match"}
              </button>
            </div>

            {analysis?.status === "analyzed" && (
              <>
                <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
                  <Stat label="Home possession" value={`${analysis.home_possession_pct.toFixed(1)}%`} />
                  <Stat label="Away possession" value={`${analysis.away_possession_pct.toFixed(1)}%`} />
                  <Stat label="Events" value={String(analysis.events.length)} />
                  <Stat label="Tracked players" value={String(analysis.players.length)} />
                </div>
                {analysis.players.length > 0 && (
                  <div className="mt-5 overflow-x-auto">
                    <table className="w-full text-left text-sm">
                      <thead className="text-xs uppercase tracking-wide text-slate-500">
                        <tr><th className="pb-2 font-medium">Player</th><th className="pb-2 font-medium">Touches</th><th className="pb-2 font-medium">Distance</th><th className="pb-2 font-medium">Avg / max speed</th></tr>
                      </thead>
                      <tbody>{analysis.players.map((player) => (
                        <tr key={player.track_id} className="border-t border-slate-800 text-slate-300">
                          <td className="py-2">#{player.jersey_number ?? "—"} · track {player.track_id}</td>
                          <td className="py-2">{player.touches}</td>
                          <td className="py-2">{player.distance_meters.toFixed(1)} m</td>
                          <td className="py-2">{player.average_speed_mps.toFixed(1)} / {player.max_speed_mps.toFixed(1)} m/s</td>
                        </tr>
                      ))}</tbody>
                    </table>
                  </div>
                )}
                {analysis.events.length > 0 && (
                  <div className="mt-5 border-t border-slate-800 pt-5">
                    <h3 className="font-medium text-slate-200">Key moments</h3>
                    <div className="mt-3 space-y-2">{analysis.events.map((event, index) => (
                      <button
                        key={`${event.timestamp_seconds}-${index}`}
                        type="button"
                        onClick={() => {
                          if (videoElement.current) {
                            videoElement.current.currentTime = event.timestamp_seconds;
                            void videoElement.current.play();
                          }
                        }}
                        className="flex w-full items-center justify-between rounded-md border border-slate-800 px-3 py-2 text-left text-sm text-slate-300 hover:bg-slate-800"
                      >
                        <span>{event.event_type === "pass" ? "Pass" : "Possession loss"} · track {event.track_id ?? "—"}</span>
                        <span className="text-slate-500">{event.timestamp_seconds}s</span>
                      </button>
                    ))}</div>
                  </div>
                )}
              </>
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

type TacticalTeam = Awaited<ReturnType<typeof api.getTactical>>;

function TacticalPitch({
  team,
  players,
  home,
  away,
}: {
  team?: "home" | "away" | "both";
  players?: TacticalTeam["players"];
  home?: TacticalTeam;
  away?: TacticalTeam;
}) {
  const renderPlayers = (teamPlayers: TacticalTeam["players"], side: "home" | "away") =>
    teamPlayers.map((player) => {
      const x = side === "home" ? 5 + player.average_x * 47 : 100 - player.average_x * 47;
      const y = 5 + player.average_y * 58;
      return (
        <g key={`${side}-${player.track_id}`}>
          <circle cx={x} cy={y} r="2.7" fill={side === "home" ? "#10b981" : "#38bdf8"} stroke="#f8fafc" strokeWidth="0.45" />
          <text x={x} y={y + 0.95} textAnchor="middle" fontSize="2.7" fill="white">{player.jersey_number ?? `?${player.track_id}`}</text>
        </g>
      );
    });

  return (
    <svg viewBox="0 0 105 68" className="mt-5 aspect-[105/68] w-full max-w-3xl rounded-sm border border-emerald-950 bg-emerald-700" role="img" aria-label="Tactical football pitch">
      <defs>
        <pattern id="pitch-stripes" width="14" height="68" patternUnits="userSpaceOnUse">
          <rect width="7" height="68" fill="#15803d" />
          <rect x="7" width="7" height="68" fill="#166534" />
        </pattern>
      </defs>
      <rect width="105" height="68" fill="url(#pitch-stripes)" />
      <rect x="2" y="2" width="101" height="64" fill="none" stroke="#f8fafc" strokeWidth="0.7" />
      <line x1="52.5" y1="2" x2="52.5" y2="66" stroke="#f8fafc" strokeWidth="0.55" />
      <circle cx="52.5" cy="34" r="9.15" fill="none" stroke="#f8fafc" strokeWidth="0.55" />
      <circle cx="52.5" cy="34" r="0.7" fill="#f8fafc" />
      <rect x="2" y="13.84" width="16.5" height="40.32" fill="none" stroke="#f8fafc" strokeWidth="0.55" />
      <rect x="86.5" y="13.84" width="16.5" height="40.32" fill="none" stroke="#f8fafc" strokeWidth="0.55" />
      <rect x="2" y="24.84" width="5.5" height="18.32" fill="none" stroke="#f8fafc" strokeWidth="0.55" />
      <rect x="97.5" y="24.84" width="5.5" height="18.32" fill="none" stroke="#f8fafc" strokeWidth="0.55" />
      <circle cx="13" cy="34" r="0.65" fill="#f8fafc" />
      <circle cx="92" cy="34" r="0.65" fill="#f8fafc" />
      <path d="M2 2 A4 4 0 0 1 6 6 M103 2 A4 4 0 0 0 99 6 M2 66 A4 4 0 0 0 6 62 M103 66 A4 4 0 0 1 99 62" fill="none" stroke="#f8fafc" strokeWidth="0.55" />
      {home && away ? <>{renderPlayers(home.players, "home")}{renderPlayers(away.players, "away")}</> : renderPlayers(players ?? [], team === "away" ? "away" : "home")}
    </svg>
  );
}

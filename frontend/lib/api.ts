const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type MatchStatus = "pending" | "uploaded" | "processing" | "analyzed" | "failed";
export type VideoStatus =
  | "uploading"
  | "uploaded"
  | "metadata_extracted"
  | "processing"
  | "analyzed"
  | "failed";

export interface Detection {
  id: number;
  video_id: number;
  frame_timestamp: number;
  bounding_box: { x: number; y: number; width: number; height: number };
  class: "player" | "ball";
  confidence: number;
  track_id: number | null;
  team_color_cluster: number | null;
  dominant_rgb: number[] | null;
  jersey_number: number | null;
  jersey_number_confidence: number | null;
  created_at: string;
}

export interface DetectionStatus {
  video_id: number;
  status: VideoStatus;
  detections_count: number;
  detections: Detection[];
}

export type TeamClusterRole = "home" | "away" | "referee";

export interface TeamClusterAssignment {
  id: number;
  cluster_id: number;
  role: TeamClusterRole;
  team_id: number | null;
  detections_count: number;
}

export interface PlayerMetric {
  track_id: number;
  team_color_cluster: number | null;
  jersey_number: number | null;
  touches: number;
  distance_meters: number;
  average_speed_mps: number;
  max_speed_mps: number;
}

export interface MatchAnalysis {
  status: string;
  home_possession_pct: number;
  away_possession_pct: number;
  players: PlayerMetric[];
  events: Array<{ event_type: string; timestamp_seconds: number; track_id: number | null; description: string | null }>;
}

export interface Team {
  id: number;
  name: string;
  short_name: string | null;
  country: string | null;
  created_at: string;
}

export interface Video {
  id: number;
  match_id: number;
  original_filename: string;
  file_size_bytes: number | null;
  content_type: string | null;
  duration_seconds: number | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  video_codec: string | null;
  audio_codec: string | null;
  bitrate: number | null;
  status: VideoStatus;
  error_message: string | null;
  uploaded_at: string;
  stream_url: string;
}

export interface Match {
  id: number;
  name: string | null;
  home_team_id: number | null;
  away_team_id: number | null;
  competition: string | null;
  match_date: string | null;
  home_score: number | null;
  away_score: number | null;
  status: MatchStatus;
  created_at: string;
  updated_at: string;
}

export interface MatchDetail extends Match {
  home_team: Team | null;
  away_team: Team | null;
  videos: Video[];
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: {
      Accept: "application/json",
      ...(options?.body && !(options.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
      ...options?.headers,
    },
    cache: "no-store",
  });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      // ignore non-JSON error bodies
    }
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) {
    return undefined as T;
  }
  return res.json() as Promise<T>;
}

export function mediaUrl(streamUrl: string): string {
  return `${API_URL}${streamUrl}`;
}

export const api = {
  listTeams: () => request<Team[]>("/api/teams"),
  createTeam: (data: { name: string; short_name?: string; country?: string }) =>
    request<Team>("/api/teams", { method: "POST", body: JSON.stringify(data) }),

  listMatches: () => request<Match[]>("/api/matches"),
  getMatch: (id: number) => request<MatchDetail>(`/api/matches/${id}`),
  createMatch: (data: {
    name?: string;
    home_team_id?: number;
    away_team_id?: number;
    competition?: string;
    match_date?: string;
  }) => request<MatchDetail>("/api/matches", { method: "POST", body: JSON.stringify(data) }),
  deleteMatch: (id: number) => request<void>(`/api/matches/${id}`, { method: "DELETE" }),

  uploadVideo: (matchId: number, file: File, onProgress?: (pct: number) => void) =>
    uploadWithProgress(matchId, file, onProgress),
  startDetection: (videoId: number) =>
    request<DetectionStatus>(`/api/videos/${videoId}/detection`, { method: "POST" }),
  getDetectionStatus: (videoId: number) =>
    request<DetectionStatus>(`/api/videos/${videoId}/detection`),
  startEnrichment: (videoId: number) =>
    request<DetectionStatus>(`/api/videos/${videoId}/enrichment`, { method: "POST" }),
  getTeamClusters: (matchId: number) =>
    request<TeamClusterAssignment[]>(`/api/matches/${matchId}/team-clusters`),
  saveTeamClusters: (matchId: number, assignments: Array<{ cluster_id: number; role: TeamClusterRole; team_id: number | null }>) =>
    request<TeamClusterAssignment[]>(`/api/matches/${matchId}/team-clusters`, {
      method: "PUT",
      body: JSON.stringify(assignments),
    }),
  updateDetection: (detectionId: number, jerseyNumber: number | null) =>
    request<Detection>(`/api/detections/${detectionId}`, {
      method: "PATCH",
      body: JSON.stringify({ jersey_number: jerseyNumber }),
    }),
  startAnalysis: (matchId: number) =>
    request<MatchAnalysis>(`/api/matches/${matchId}/analysis`, { method: "POST" }),
  getAnalysis: (matchId: number) => request<MatchAnalysis>(`/api/matches/${matchId}/analysis`),
};

function uploadWithProgress(
  matchId: number,
  file: File,
  onProgress?: (pct: number) => void
): Promise<Video> {
  return new Promise((resolve, reject) => {
    const formData = new FormData();
    formData.append("file", file);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_URL}/api/matches/${matchId}/video`);
    xhr.responseType = "json";

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && onProgress) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(xhr.response as Video);
      } else {
        const detail = xhr.response?.detail ?? xhr.statusText;
        reject(new ApiError(xhr.status, detail));
      }
    };

    xhr.onerror = () => reject(new ApiError(0, "Network error during upload"));

    xhr.send(formData);
  });
}

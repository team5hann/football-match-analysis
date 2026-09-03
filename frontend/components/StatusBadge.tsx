import type { MatchStatus } from "@/lib/api";

const STYLES: Record<MatchStatus, string> = {
  pending: "bg-slate-700 text-slate-200",
  uploaded: "bg-blue-900 text-blue-200",
  processing: "bg-amber-900 text-amber-200",
  analyzed: "bg-emerald-900 text-emerald-200",
  failed: "bg-red-900 text-red-200",
};

const LABELS: Record<MatchStatus, string> = {
  pending: "Waiting for video",
  uploaded: "Video ready",
  processing: "Processing",
  analyzed: "Analyzed",
  failed: "Failed",
};

export default function StatusBadge({ status }: { status: MatchStatus }) {
  return (
    <span className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium ${STYLES[status]}`}>
      {LABELS[status]}
    </span>
  );
}

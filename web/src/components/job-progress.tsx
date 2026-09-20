import { Fragment } from "react";

export interface JobSnapshot {
  job_id: string;
  lecture_id: string | null;
  status: "queued" | "running" | "done" | "error";
  stage: string | null;
  message: string;
  progress: number | null;
  error: string | null;
  started_at: number;
  elapsed_seconds: number;
}

const STAGES: { key: string; label: string }[] = [
  { key: "slides", label: "Slides" },
  { key: "audio", label: "Audio" },
  { key: "transcribe", label: "Transcribe" },
  { key: "synthesize", label: "Synthesize" },
  { key: "emit", label: "Emit" },
];

/** The backend's `progress` field is only ever a real fraction during the
 * transcribe stage (whisper reports it segment-by-segment) — every other
 * stage reports `null` throughout and only becomes "done" instantly, so a
 * single continuous 0-100% bar across the whole pipeline would just sit at
 * 0% then jump to 100%. This renders stage *position* instead (a 5-step
 * tracker) and only shows a fractional sub-bar for the one stage that
 * actually has one. */
export function JobProgress({ job }: { job: JobSnapshot }) {
  const currentIndex = job.status === "done" ? STAGES.length : STAGES.findIndex((s) => s.key === job.stage);

  function stepState(index: number): "completed" | "current" | "error" | "upcoming" {
    if (job.status === "error" && index === currentIndex) return "error";
    if (index < currentIndex) return "completed";
    if (index === currentIndex && job.status !== "queued") return "current";
    return "upcoming";
  }

  return (
    <div className="job-card">
      <div className="stage-row">
        {STAGES.map((stage, index) => (
          <Fragment key={stage.key}>
            {index > 0 && (
              <div className={`stage-connector${index <= currentIndex ? " completed" : ""}`} />
            )}
            <div className={`stage-step ${stepState(index)}`}>
              <div className="stage-dot">{stepState(index) === "completed" ? "✓" : index + 1}</div>
              <div className="stage-label">{stage.label}</div>
            </div>
          </Fragment>
        ))}
      </div>

      {job.status === "queued" && <p className="job-message">Queued…</p>}

      {job.stage === "transcribe" && job.status === "running" && job.progress != null && (
        <div className="progress-row">
          <div className="progress-track">
            <div className="progress-fill" style={{ width: `${Math.round(job.progress * 100)}%` }} />
          </div>
          <span className="progress-pct">{Math.round(job.progress * 100)}%</span>
        </div>
      )}

      {job.message && job.status !== "error" && <p className="job-message">{job.message}</p>}
      {job.status === "error" && <p className="error-message">{job.error}</p>}
    </div>
  );
}

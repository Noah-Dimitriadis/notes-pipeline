"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

interface Lecture {
  lecture_id: string;
  title: string;
  date: string | null;
  has_notes: boolean;
  notes_generated_at: string | null;
}

interface JobSnapshot {
  job_id: string;
  status: "queued" | "running" | "done" | "error";
  stage: string | null;
  message: string;
  progress: number | null;
  error: string | null;
}

export function LecturesView() {
  const jobId = useSearchParams().get("job");
  const [lectures, setLectures] = useState<Lecture[] | null>(null);
  const [job, setJob] = useState<JobSnapshot | null>(null);
  const pollHandle = useRef<ReturnType<typeof setInterval> | null>(null);

  async function refreshLectures() {
    const res = await fetch("/api/lectures", { cache: "no-store" });
    if (res.ok) setLectures(await res.json());
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch resolves asynchronously, no synchronous cascading render
    refreshLectures();
  }, []);

  // Track the just-uploaded job (passed via ?job=) client-side and poll it
  // until it finishes — the api service tracks job_id -> lecture_id, we
  // just need to know when to stop polling and refresh the lecture list.
  useEffect(() => {
    if (!jobId) return;
    async function poll() {
      const res = await fetch(`/api/jobs/${encodeURIComponent(jobId!)}`, { cache: "no-store" });
      if (!res.ok) return;
      const snapshot: JobSnapshot = await res.json();
      setJob(snapshot);
      if (snapshot.status === "done" || snapshot.status === "error") {
        if (pollHandle.current) clearInterval(pollHandle.current);
        refreshLectures();
      }
    }
    poll();
    pollHandle.current = setInterval(poll, 2000);
    return () => {
      if (pollHandle.current) clearInterval(pollHandle.current);
    };
  }, [jobId]);

  return (
    <div>
      {job && job.status !== "done" && (
        <p>
          Job {job.job_id.slice(0, 8)}: {job.status}
          {job.stage ? ` — ${job.stage}` : ""}
          {job.message ? ` (${job.message})` : ""}
          {job.progress != null ? ` — ${Math.round(job.progress * 100)}%` : ""}
        </p>
      )}
      {job && job.status === "error" && <p className="error-message">{job.error}</p>}

      {lectures === null ? (
        <p>Loading…</p>
      ) : lectures.length === 0 ? (
        <p>No lectures yet.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Title</th>
              <th>Date</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>
            {lectures.map((lec) => (
              <tr key={lec.lecture_id}>
                <td>{lec.title}</td>
                <td>{lec.date ?? "—"}</td>
                <td>
                  {lec.has_notes ? (
                    <a href={`/api/lectures/${encodeURIComponent(lec.lecture_id)}/notes`}>
                      Download
                    </a>
                  ) : (
                    "building…"
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

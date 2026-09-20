"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { JobProgress, type JobSnapshot } from "@/components/job-progress";

interface Lecture {
  lecture_id: string;
  title: string;
  date: string | null;
  has_notes: boolean;
  notes_generated_at: string | null;
}

export function LecturesView() {
  const jobId = useSearchParams().get("job");
  const [lectures, setLectures] = useState<Lecture[] | null>(null);
  const [job, setJob] = useState<JobSnapshot | null>(null);
  const [jobHistory, setJobHistory] = useState<JobSnapshot[] | null>(null);
  const pollHandle = useRef<ReturnType<typeof setInterval> | null>(null);

  async function refreshLectures() {
    const res = await fetch("/api/lectures", { cache: "no-store" });
    if (res.ok) setLectures(await res.json());
  }

  // Persistent job history — every upload this account has ever started,
  // not just the one tracked via ?job= in this tab. Survives a page
  // refresh and an `api` restart, since it's read from the `jobs` table.
  async function refreshJobHistory() {
    const res = await fetch("/api/jobs", { cache: "no-store" });
    if (res.ok) setJobHistory(await res.json());
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch resolves asynchronously, no synchronous cascading render
    refreshLectures();
    refreshJobHistory();
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
      refreshJobHistory();
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
      {job && job.status !== "done" && <JobProgress job={job} />}

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

      <h2>Job history</h2>
      {jobHistory === null ? (
        <p>Loading…</p>
      ) : jobHistory.length === 0 ? (
        <p>No uploads yet.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Started</th>
              <th>Status</th>
              <th>Stage</th>
              <th>Message</th>
              <th>Elapsed</th>
            </tr>
          </thead>
          <tbody>
            {jobHistory.map((j) => (
              <tr key={j.job_id}>
                <td>{new Date(j.started_at * 1000).toLocaleString()}</td>
                <td>
                  <span className="badge">{j.status}</span>
                </td>
                <td>{j.stage ?? "—"}</td>
                <td className={j.status === "error" ? "error-message" : undefined}>
                  {j.status === "error" ? j.error : j.message || "—"}
                </td>
                <td>{Math.round(j.elapsed_seconds)}s</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

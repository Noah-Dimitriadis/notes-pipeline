"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Dropzone } from "@/components/dropzone";

export function UploadForm() {
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploadPct, setUploadPct] = useState<number | null>(null);

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    setUploadPct(0);

    // XMLHttpRequest, not fetch — fetch has no cross-browser way to report
    // upload progress; xhr.upload.onprogress is what actually drives the
    // bytes-sent bar below for a large audio file.
    const formData = new FormData(event.currentTarget);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/lectures");

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) setUploadPct(Math.round((e.loaded / e.total) * 100));
    };

    xhr.onload = () => {
      let body: { job_id?: string; detail?: string; error?: string } = {};
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        // non-JSON error body, fall through to the generic message below
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        setError(body.detail || body.error || `Upload failed (${xhr.status})`);
        setSubmitting(false);
        setUploadPct(null);
        return;
      }
      router.push(`/lectures?job=${encodeURIComponent(body.job_id!)}`);
    };

    xhr.onerror = () => {
      setError("Upload failed — check your connection and try again.");
      setSubmitting(false);
      setUploadPct(null);
    };

    xhr.send(formData);
  }

  return (
    <form onSubmit={handleSubmit}>
      {error && <p className="error-message">{error}</p>}
      <div className="field">
        <label htmlFor="title">Title</label>
        <input type="text" id="title" name="title" required disabled={submitting} />
      </div>
      <Dropzone id="audio" name="audio" label="Audio (required)" accept="audio/*" required multiple disabled={submitting} hint="Drop one or more recordings, or click to browse — they're joined in the order listed" />
      <Dropzone id="deck" name="deck" label="Slide decks (optional)" accept=".pptx,.pdf" multiple disabled={submitting} hint="Drop one or more .pptx / .pdf decks, or click to browse — leave empty if there are no slides" />
      <Dropzone id="notes" name="notes" label="Your notes (optional)" accept=".md,.pdf,.txt" disabled={submitting} hint="Drop a .md, .pdf, or .txt, or click to browse" />
      <Dropzone id="assets" name="assets" label="Other assets (optional)" accept=".md,.pdf,.txt" multiple disabled={submitting} hint="Readings, handouts, or other reference material (.md, .pdf, .txt)" />

      {uploadPct != null && (
        <div className="progress-row">
          <div className="progress-track">
            <div className="progress-fill" style={{ width: `${uploadPct}%` }} />
          </div>
          <span className="progress-pct">{uploadPct}%</span>
        </div>
      )}

      <button type="submit" disabled={submitting}>
        {submitting ? (uploadPct === 100 ? "Processing…" : "Uploading…") : "Upload"}
      </button>
    </form>
  );
}

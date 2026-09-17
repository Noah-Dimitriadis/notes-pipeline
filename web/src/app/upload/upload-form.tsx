"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export function UploadForm() {
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      // Passing the <form>'s FormData straight to fetch lets the browser
      // stream the audio file from disk rather than reading it into a JS
      // string/buffer first — the same no-buffering constraint the server
      // side's route handler honors by forwarding req.body untouched.
      const formData = new FormData(event.currentTarget);
      const res = await fetch("/api/lectures", { method: "POST", body: formData });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || body.error || `Upload failed (${res.status})`);
      }
      const { job_id } = await res.json();
      router.push(`/lectures?job=${encodeURIComponent(job_id)}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed.");
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      {error && <p className="error-message">{error}</p>}
      <div className="field">
        <label htmlFor="title">Title</label>
        <input type="text" id="title" name="title" required disabled={submitting} />
      </div>
      <div className="field">
        <label htmlFor="audio">Audio (required)</label>
        <input type="file" id="audio" name="audio" accept="audio/*" required disabled={submitting} />
      </div>
      <div className="field">
        <label htmlFor="deck">Slide deck (optional, .pptx/.pdf)</label>
        <input type="file" id="deck" name="deck" accept=".pptx,.pdf" disabled={submitting} />
      </div>
      <div className="field">
        <label htmlFor="notes">Your notes (optional, .md/.pdf/.txt)</label>
        <input type="file" id="notes" name="notes" accept=".md,.pdf,.txt" disabled={submitting} />
      </div>
      <button type="submit" disabled={submitting}>
        {submitting ? "Uploading…" : "Upload"}
      </button>
    </form>
  );
}

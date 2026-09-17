"use client";

import { useState } from "react";

export function ApiKeyForm() {
  const [key, setKey] = useState("");
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setStatus("saving");
    try {
      const res = await fetch("/api/me/anthropic-key", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ api_key: key }),
      });
      if (!res.ok) throw new Error();
      // Fire-and-forget from the UI's perspective: clear the field, never
      // echo the key back, just confirm it saved.
      setKey("");
      setStatus("saved");
    } catch {
      setStatus("error");
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <div className="field">
        <label htmlFor="api_key">Anthropic API key</label>
        <input
          type="password"
          id="api_key"
          name="api_key"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder="sk-ant-..."
          required
          disabled={status === "saving"}
        />
      </div>
      <button type="submit" disabled={status === "saving" || !key}>
        Save
      </button>
      {status === "saved" && <p>Saved.</p>}
      {status === "error" && <p className="error-message">Could not save the key.</p>}
    </form>
  );
}

import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Minimal production image (deploy/web/Dockerfile copies .next/standalone
  // rather than the whole node_modules tree).
  output: "standalone",
  // better-sqlite3 is a native addon (a compiled .node binary) — it must
  // stay a real `require()` rather than get pulled into the server bundle,
  // or the compiled binary can't be resolved at runtime.
  serverExternalPackages: ["better-sqlite3"],
  // Don't auto-generate AGENTS.md/CLAUDE.md in this repo — it already has
  // its own hand-written CLAUDE.md conventions (see the repo root).
  agentRules: false,
};

export default nextConfig;

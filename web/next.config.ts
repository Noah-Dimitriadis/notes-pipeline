import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Minimal production image (deploy/web/Dockerfile copies .next/standalone
  // rather than the whole node_modules tree).
  output: "standalone",
  // better-sqlite3 is a native addon (a compiled .node binary) — it must
  // stay a real `require()` rather than get pulled into the server bundle,
  // or the compiled binary can't be resolved at runtime.
  serverExternalPackages: ["better-sqlite3"],
  // proxy.ts runs on every request, so Next buffers each request body (10MB
  // cap by default, silently truncating past it) to let the proxy read it —
  // which would truncate lecture audio uploaded to /api/lectures. The proxy
  // never touches bodies, so raise the cap well above any lecture recording.
  experimental: {
    middlewareClientMaxBodySize: "1gb",
  },
  // Don't auto-generate AGENTS.md/CLAUDE.md in this repo — it already has
  // its own hand-written CLAUDE.md conventions (see the repo root).
  agentRules: false,
};

export default nextConfig;

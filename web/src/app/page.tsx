import { redirect } from "next/navigation";
import { requireSession } from "@/lib/authz";

export default async function Home() {
  // requireSession() already honors AUTH_ENABLED=false (bypass straight to
  // a valid session) and redirects to /login itself for a missing/disabled
  // real session — this page's only remaining job is where to send an
  // authenticated user next.
  await requireSession();
  const isAdminMode = process.env.APP_MODE === "admin";
  redirect(isAdminMode ? "/admin" : "/upload");
}

import { Suspense } from "react";
import { requireSession } from "@/lib/authz";
import { LecturesView } from "./lectures-view";

export default async function LecturesPage() {
  await requireSession();
  return (
    <div>
      <h1>Your lectures</h1>
      <Suspense fallback={<p>Loading…</p>}>
        <LecturesView />
      </Suspense>
    </div>
  );
}

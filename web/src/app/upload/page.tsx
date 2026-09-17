import { requireSession } from "@/lib/authz";
import { UploadForm } from "./upload-form";

export default async function UploadPage() {
  await requireSession();
  return (
    <div>
      <h1>Upload a lecture</h1>
      <UploadForm />
    </div>
  );
}

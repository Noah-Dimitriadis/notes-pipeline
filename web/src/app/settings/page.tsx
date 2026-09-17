import { requireSession } from "@/lib/authz";
import { ApiKeyForm } from "./api-key-form";

export default async function SettingsPage() {
  await requireSession();
  return (
    <div>
      <h1>Settings</h1>
      <p>Paste your own Anthropic API key. It&apos;s used only for your uploads and never shown again.</p>
      <ApiKeyForm />
    </div>
  );
}

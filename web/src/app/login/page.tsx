import { signIn } from "@/auth";

const ERROR_MESSAGES: Record<string, string> = {
  AccessDenied: "That Google account isn't on the allowlist. Ask for it to be added first.",
  disabled: "This account has been disabled.",
  forbidden: "That account doesn't have admin access.",
  Configuration: "Sign-in is misconfigured. Check the server logs.",
};

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const { error } = await searchParams;
  const isAdminMode = process.env.APP_MODE === "admin";

  return (
    <div>
      <h1>{isAdminMode ? "notes-pipeline admin" : "notes-pipeline"}</h1>
      {error && (
        <p className="error-message">{ERROR_MESSAGES[error] ?? "Sign-in failed."}</p>
      )}
      <form
        action={async () => {
          "use server";
          await signIn("google", { redirectTo: isAdminMode ? "/admin" : "/upload" });
        }}
      >
        <button type="submit">Sign in with Google</button>
      </form>
    </div>
  );
}

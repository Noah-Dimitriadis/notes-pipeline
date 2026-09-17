import { redirect } from "next/navigation";
import { auth } from "@/auth";

export default async function Home() {
  const session = await auth();
  const isAdminMode = process.env.APP_MODE === "admin";

  if (!session?.user?.email) redirect("/login");
  if (session.user.disabled) redirect("/login?error=disabled");
  redirect(isAdminMode ? "/admin" : "/upload");
}

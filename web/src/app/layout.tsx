import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { signOut } from "@/auth";
import { currentSession } from "@/lib/authz";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "notes-pipeline",
  description: "Lecture notes pipeline",
};

export default async function RootLayout({ children }: LayoutProps<"/">) {
  const session = await currentSession();
  const isAdminMode = process.env.APP_MODE === "admin";

  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable}`}>
      <body>
        {session?.user && !session.user.disabled && (
          <header className="site-header">
            <nav>
              {isAdminMode ? (
                <a href="/admin">Users</a>
              ) : (
                <>
                  <a href="/upload">Upload</a>
                  <a href="/lectures">Lectures</a>
                </>
              )}
            </nav>
            <div className="right-cluster">
              {!isAdminMode && <a href="/settings">Settings</a>}
              <form
                action={async () => {
                  "use server";
                  await signOut({ redirectTo: "/login" });
                }}
              >
                <span className="user-email">{session.user.email}</span>
                <button type="submit" className="link-button">
                  Sign out
                </button>
              </form>
            </div>
          </header>
        )}
        <main className="page-container">{children}</main>
      </body>
    </html>
  );
}

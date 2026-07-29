import type { Metadata } from "next";
import { headers } from "next/headers";
import { CopilotProvider } from "@/components/copilot-provider";
import { Header } from "@/components/header";
import { githubAuthEnabled, userFromHeaders } from "@/lib/auth";
import "./design-system.css";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "MAFIA",
    template: "%s | MAFIA",
  },
  description: "Source-grounded engineering workflows.",
};

export default async function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const user = githubAuthEnabled() ? userFromHeaders(await headers()) : undefined;
  return (
    <html lang="en">
      <body>
        <CopilotProvider>
          <div className="ph-shell">
            <Header user={user} />
            <main className="ph-main">{children}</main>
          </div>
        </CopilotProvider>
      </body>
    </html>
  );
}

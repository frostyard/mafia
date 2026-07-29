import type { Metadata } from "next";
import { CopilotProvider } from "@/components/copilot-provider";
import { Header } from "@/components/header";
import "./design-system.css";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "MAFIA",
    template: "%s | MAFIA",
  },
  description: "Source-grounded engineering workflows.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <CopilotProvider>
          <div className="ph-shell">
            <Header />
            <main className="ph-main">{children}</main>
          </div>
        </CopilotProvider>
      </body>
    </html>
  );
}

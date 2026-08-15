import type { Metadata } from "next";
import { ThemeProvider } from "@ascras/ui";
import "@ascras/ui/styles.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "ASCRAS Portal",
  description: "Upload recordings and review call QA results.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        {/* Dark by default: reviewers spend hours in here. Both themes work. */}
        <ThemeProvider defaultTheme="dark" storageKey="ascras-portal-theme">
          {children}
        </ThemeProvider>
      </body>
    </html>
  );
}

import type { Metadata } from "next";
import { ThemeProvider } from "@ascras/ui";
import "@ascras/ui/styles.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "ASCRAS — Automated Sales Call Review and Analysis System",
  description:
    "Speaker-attributed transcripts, compliance findings backed by timestamped evidence, and QA scorecards for sales calls. Runs on your own hardware.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        {/* The marketing site sells in light; the portal and admin default to
            dark because people work in them for hours. */}
        <ThemeProvider defaultTheme="light">{children}</ThemeProvider>
      </body>
    </html>
  );
}

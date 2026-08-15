import type { Metadata } from "next";
import { ThemeProvider } from "@ascras/ui";
import "@ascras/ui/styles.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "ASCRAS Admin",
  description: "Accounts, bots and payment records.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <ThemeProvider defaultTheme="dark" storageKey="ascras-admin-theme">
          {children}
        </ThemeProvider>
      </body>
    </html>
  );
}

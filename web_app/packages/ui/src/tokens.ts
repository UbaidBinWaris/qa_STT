/**
 * ASCRAS design tokens — the single source of colour for landing, portal and admin.
 *
 * The rule that matters: green, amber and red are RESERVED. Throughout this
 * product they carry meaning a reviewer relies on — a word the recogniser
 * verified, a word it was unsure of, two decoders that disagreed, a compliance
 * violation. If the brand also used those hues, a reviewer could no longer tell
 * "this is our button" from "this is a problem in your call".
 *
 * So the brand lives in indigo/violet, and semantic colour is never decorative.
 */

export const brand = {
  50: "#EEF0FF",
  100: "#E0E3FF",
  300: "#A5B0FF",
  500: "#6366F1", // primary — buttons, links, active nav
  600: "#5457E0",
  700: "#4338CA",
  900: "#2E2A80",
} as const;

/** Reserved. Never use these for branding, decoration or emphasis. */
export const semantic = {
  verified: "#22C55E", // transcript confirmed by the second pass
  uncertain: "#F59E0B", // low confidence, or recovered from dropped audio
  conflict: "#EF4444", // decoders disagreed, or a compliance violation
  info: "#38BDF8",
} as const;

export const dark = {
  canvas: "#0B0F19",
  surface: "#131B2E",
  surfaceRaised: "#1B2439",
  border: "rgba(255,255,255,0.08)",
  borderStrong: "rgba(255,255,255,0.16)",
  text: "#F1F5F9",
  textMuted: "#94A3B8",
  brandWash: "rgba(99,102,241,0.12)",
} as const;

export const light = {
  canvas: "#FFFFFF",
  surface: "#F8FAFC",
  surfaceRaised: "#FFFFFF",
  border: "rgba(11,15,25,0.10)",
  borderStrong: "rgba(11,15,25,0.20)",
  text: "#0B0F19",
  textMuted: "#5A6478",
  brandWash: "rgba(99,102,241,0.08)",
} as const;

export type ThemeName = "light" | "dark";

/** CSS custom properties, emitted per theme so components never branch on theme. */
export function cssVars(theme: ThemeName): Record<string, string> {
  const t = theme === "dark" ? dark : light;
  return {
    "--canvas": t.canvas,
    "--surface": t.surface,
    "--surface-raised": t.surfaceRaised,
    "--border": t.border,
    "--border-strong": t.borderStrong,
    "--text": t.text,
    "--text-muted": t.textMuted,
    "--brand": brand[500],
    "--brand-hover": brand[300],
    "--brand-wash": t.brandWash,
    "--verified": semantic.verified,
    "--uncertain": semantic.uncertain,
    "--conflict": semantic.conflict,
    "--info": semantic.info,
  };
}

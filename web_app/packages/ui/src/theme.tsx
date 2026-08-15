"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { cssVars, type ThemeName } from "./tokens";

interface ThemeContextValue {
  theme: ThemeName;
  toggle: () => void;
  setTheme: (t: ThemeName) => void;
}

const ThemeContext = createContext<ThemeContextValue>({
  theme: "dark",
  toggle: () => {},
  setTheme: () => {},
});

export const useTheme = () => useContext(ThemeContext);

/**
 * Applies the palette as CSS custom properties on <html>, so components style
 * themselves with var(--brand) and never branch on the current theme.
 *
 * `defaultTheme` differs per app: the landing site sells in light, the portal
 * and admin are worked in for hours and default to dark.
 */
export function ThemeProvider({
  children,
  defaultTheme = "dark",
  storageKey = "ascras-theme",
}: {
  children: ReactNode;
  defaultTheme?: ThemeName;
  storageKey?: string;
}) {
  const [theme, setThemeState] = useState<ThemeName>(defaultTheme);

  // Read the stored preference after mount. Reading during render would produce
  // markup on the server that disagrees with the client and hydration would warn.
  useEffect(() => {
    const stored = window.localStorage.getItem(storageKey) as ThemeName | null;
    if (stored === "light" || stored === "dark") setThemeState(stored);
  }, [storageKey]);

  useEffect(() => {
    const root = document.documentElement;
    for (const [key, value] of Object.entries(cssVars(theme))) {
      root.style.setProperty(key, value);
    }
    root.dataset.theme = theme;
    root.style.colorScheme = theme;
  }, [theme]);

  const setTheme = useCallback(
    (next: ThemeName) => {
      setThemeState(next);
      window.localStorage.setItem(storageKey, next);
    },
    [storageKey],
  );

  const toggle = useCallback(
    () => setTheme(theme === "dark" ? "light" : "dark"),
    [theme, setTheme],
  );

  return (
    <ThemeContext.Provider value={{ theme, toggle, setTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function ThemeToggle({ className = "" }: { className?: string }) {
  const { theme, toggle } = useTheme();
  return (
    <button
      type="button"
      onClick={toggle}
      className={`ascras-theme-toggle ${className}`}
      aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
      title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
    >
      {theme === "dark" ? "☀" : "☾"}
    </button>
  );
}

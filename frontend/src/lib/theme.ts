import { useCallback, useEffect, useState } from "react";

export type Theme = "dark" | "light";
const KEY = "tradecrm.theme";

export function initialTheme(): Theme {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === "dark" || saved === "light") return saved;
  } catch {
    /* depolama kapali olabilir */
  }
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(initialTheme);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem(KEY, theme);
    } catch {
      /* yoksay */
    }
  }, [theme]);
  const toggle = useCallback(() => setTheme((t) => (t === "dark" ? "light" : "dark")), []);
  return { theme, toggle };
}

/** CSS token'ini ("46 189 133") grafik kutuphanesinin anladigi rgba()'ya cevirir. */
export function tokenColor(name: string, alpha = 1): string {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim();
  const [r = "128", g = "128", b = "128"] = raw.split(/\s+/);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

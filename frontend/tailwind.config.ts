import type { Config } from "tailwindcss";

// Design tokens, named per the design plan: a dark, dense fintech-terminal
// aesthetic. Every color here is referenced by semantic name in components
// (bg-void, text-primary, signal-fraud, etc.) — never raw hex inline —
// so the palette stays a single source of truth and any future re-theming
// is a one-file change.
const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        void: "#0A0E14",       // page background - cool near-black, not pure black
        raised: "#11161F",     // panel/card surfaces, one step up from void
        "raised-2": "#161C28", // a second elevation step for nested panels
        hairline: "#1F2733",   // borders/dividers - barely-there on dark bg
        "text-primary": "#E4E8EE",
        "text-secondary": "#9AA3B2",
        "text-muted": "#5C6678",
        fraud: "#FF4757",      // signal-fraud: reserved exclusively for fraud-positive data
        "fraud-dim": "#7A2530", // low-intensity fraud signal (e.g. heatmap low end)
        safe: "#2ED9A3",       // signal-safe: legitimate/safe metrics
        "safe-dim": "#1A5C49",
        amber: "#F0A640",      // reserved for warnings / detection-rule discrepancies
      },
      fontFamily: {
        mono: ["var(--font-plex-mono)", "ui-monospace", "monospace"],
        sans: ["var(--font-inter)", "ui-sans-serif", "system-ui"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem" }],
      },
      animation: {
        "pulse-fraud": "pulse-fraud 2s ease-in-out infinite",
      },
      keyframes: {
        "pulse-fraud": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.55" },
        },
      },
    },
  },
  plugins: [],
};

export default config;

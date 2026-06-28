import type { Metadata } from "next";
import { IBM_Plex_Mono, Inter } from "next/font/google";
import Link from "next/link";
import "./globals.css";

// next/font self-hosts these at build time (no runtime request to
// fonts.googleapis.com, no render-blocking @import, no layout shift from
// a late-loading webfont) — this matters for a dashboard meant to feel
// instant on load, not just visually correct once everything settles.
const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-mono",
  display: "swap",
});

const inter = Inter({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-inter",
  display: "swap",
});

export const metadata: Metadata = {
  title: "FinGuard Analytics",
  description:
    "Fraud and transaction analytics over the PaySim dataset — live fraud-pattern exploration backed by precomputed materialized views.",
};

const NAV_ITEMS = [
  { href: "/", label: "overview" },
  { href: "/patterns", label: "patterns" },
  { href: "/velocity", label: "velocity" },
] as const;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${plexMono.variable} ${inter.variable}`}>
      <body className="font-sans antialiased">
        <div className="flex min-h-screen flex-col">
          <header className="sticky top-0 z-20 border-b border-hairline bg-void/95 backdrop-blur-sm">
            <div className="mx-auto flex h-14 max-w-[1400px] items-center justify-between px-6">
              <Link
                href="/"
                className="flex items-center gap-2.5 font-mono text-sm font-medium tracking-tight text-text-primary"
              >
                <span
                  aria-hidden="true"
                  className="inline-block h-2 w-2 animate-pulse-fraud rounded-full bg-fraud"
                />
                FINGUARD
                <span className="text-text-muted">/</span>
                <span className="text-text-muted">analytics</span>
              </Link>

              <nav aria-label="Main" className="flex items-center gap-1">
                {NAV_ITEMS.map((item) => (
                  <Link
                    key={item.href}
                    href={item.href}
                    className="rounded px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-text-secondary transition-colors hover:bg-raised hover:text-text-primary"
                  >
                    {item.label}
                  </Link>
                ))}
              </nav>
            </div>
          </header>

          <main className="mx-auto w-full max-w-[1400px] flex-1 px-6 py-6">
            {children}
          </main>

          <footer className="border-t border-hairline px-6 py-4">
            <p className="mx-auto max-w-[1400px] font-mono text-2xs text-text-muted">
              PaySim synthetic data · stratified sample, 100% fraud retention · read-only views, no raw table scans
            </p>
          </footer>
        </div>
      </body>
    </html>
  );
}

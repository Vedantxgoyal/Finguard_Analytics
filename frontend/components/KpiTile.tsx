import { clsx } from "clsx";

interface KpiTileProps {
  label: string;
  value: string;
  tone?: "default" | "fraud" | "safe";
  sublabel?: string;
}

/**
 * A single headline metric tile. Tone drives the value's color only —
 * label and sublabel always stay in muted/secondary ink (per the design
 * principle that text wears semantic color sparingly and deliberately,
 * never as decoration). Used in a grid of 4-6 across the overview page.
 */
export function KpiTile({ label, value, tone = "default", sublabel }: KpiTileProps) {
  return (
    <div className="rounded-lg border border-hairline bg-raised p-4">
      <p className="font-mono text-2xs uppercase tracking-wider text-text-muted">
        {label}
      </p>
      <p
        className={clsx(
          "mt-2 font-mono text-2xl font-semibold tabular-nums",
          tone === "fraud" && "text-fraud",
          tone === "safe" && "text-safe",
          tone === "default" && "text-text-primary",
        )}
      >
        {value}
      </p>
      {sublabel && (
        <p className="mt-1 font-mono text-2xs text-text-secondary">{sublabel}</p>
      )}
    </div>
  );
}

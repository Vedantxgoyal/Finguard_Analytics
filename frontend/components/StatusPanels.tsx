export function LoadingSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-2" aria-label="Loading" aria-live="polite">
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="h-16 animate-pulse rounded-lg bg-raised"
          style={{ animationDelay: `${i * 80}ms` }}
        />
      ))}
    </div>
  );
}

export function ErrorPanel({ message }: { message: string }) {
  return (
    <div
      role="alert"
      className="rounded-lg border border-fraud-dim bg-fraud-dim/20 p-4"
    >
      <p className="font-mono text-xs text-fraud">connection error</p>
      <p className="mt-1 text-sm text-text-secondary">{message}</p>
    </div>
  );
}

export function EmptyPanel({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-hairline bg-raised p-6 text-center">
      <p className="text-sm text-text-secondary">{message}</p>
    </div>
  );
}

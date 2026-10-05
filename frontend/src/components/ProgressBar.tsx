interface Props {
  value: number;
  label?: string;
}

export function ProgressBar({ value, label }: Props) {
  const pct = Math.max(0, Math.min(100, Math.round(value)));
  return (
    <div className="progress">
      <div
        className="progress-track"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-label={label ?? "Progress"}
      >
        <div className="progress-fill" style={{ width: `${pct}%` }} />
      </div>
      <span className="progress-pct">{pct}%</span>
    </div>
  );
}

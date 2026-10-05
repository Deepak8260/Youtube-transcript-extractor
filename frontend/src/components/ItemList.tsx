import { ITEM_ICON, ITEM_LABEL } from "../status";
import type { FileInfo, JobItem } from "../types";

interface Props {
  items: JobItem[];
  /** A message already shown above the list; not repeated on each row. */
  hiddenMessage?: string | null;
  /** When given, rows with a transcript get a checkbox. */
  selected?: Set<string>;
  onToggle?: (fileId: string) => void;
  onView: (file: FileInfo) => void;
}

const CODE_LABEL: Record<string, string> = {
  rate_limited: "Rate limited",
  private_video: "Private",
  age_restricted: "Age-restricted",
  whisper_error: "Whisper failed",
};

export function ItemList({ items, hiddenMessage, selected, onToggle, onView }: Props) {
  const width = Math.max(2, String(items.length).length);
  const visible = (message: string | null) => (message && message !== hiddenMessage ? message : null);
  const selectable = Boolean(selected && onToggle);

  return (
    <ul className={`items${selectable ? " items-selectable" : ""}`}>
      {items.map((item) => {
        const file = item.file;
        // "Already extracted" is said once in the summary, not on every row.
        const meta =
          item.status === "processing" ? item.stage_message : item.status === "existing" ? null : visible(item.message);
        return (
          <li key={`${item.index}-${item.video_id}`} className={`item item-${item.status}`}>
            {selectable && (
              <span className="item-check">
                {file && (
                  <input
                    type="checkbox"
                    checked={selected!.has(file.file_id)}
                    onChange={() => onToggle!(file.file_id)}
                    aria-label={`Select ${file.filename}`}
                  />
                )}
              </span>
            )}
            <span className="item-icon" aria-label={ITEM_LABEL[item.status]} title={ITEM_LABEL[item.status]}>
              {ITEM_ICON[item.status]}
            </span>
            <span className="item-index">{String(item.index).padStart(width, "0")}</span>
            <div className="item-body">
              <div className="item-title">{file ? file.filename : item.title}</div>
              {(meta || item.warnings.length > 0) && (
                <div className="item-meta">
                  {meta}
                  {item.warnings.map((w) => (
                    <div key={w} className="item-warning">
                      {w}
                    </div>
                  ))}
                </div>
              )}
            </div>
            <span className="item-source">
              {item.transcript_source ??
                (item.status === "pending"
                  ? ""
                  : (item.error_code && CODE_LABEL[item.error_code]) || ITEM_LABEL[item.status])}
            </span>
            <span className="item-actions">
              {file && (
                <button className="link-btn" onClick={() => onView(file)} aria-label={`View ${file.filename}`}>
                  View
                </button>
              )}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

"use client";

import { useRef, useState } from "react";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(1)} ${units[unitIndex]}`;
}

interface DropzoneProps {
  id: string;
  name: string;
  label: string;
  accept?: string;
  required?: boolean;
  disabled?: boolean;
  hint?: string;
}

/** A styled drag-and-drop wrapper around a real `<input type="file">` — the
 * input stays in the DOM (keyboard/screen-reader accessible, and what
 * `FormData(form)` actually reads on submit), this just makes it easier to
 * use: click-to-browse, drag-and-drop (including a file dragged in from
 * another tab, e.g. Chrome's downloads shelf), and a compact "chosen file"
 * chip with a clear button instead of the raw native file input UI. */
export function Dropzone({ id, name, label, accept, required, disabled, hint }: DropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);

  function applyFile(next: File | null) {
    setFile(next);
    if (inputRef.current && next) {
      const dt = new DataTransfer();
      dt.items.add(next);
      inputRef.current.files = dt.files;
    }
  }

  function handleDrop(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    if (disabled) return;
    const dropped = event.dataTransfer.files?.[0];
    if (dropped) applyFile(dropped);
  }

  function handleClear(event: React.MouseEvent) {
    event.stopPropagation();
    if (inputRef.current) inputRef.current.value = "";
    setFile(null);
  }

  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <div
        className={`dropzone${dragging ? " dragging" : ""}${disabled ? " disabled" : ""}`}
        onClick={() => !disabled && inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        role="button"
        tabIndex={disabled ? -1 : 0}
        onKeyDown={(e) => {
          if (!disabled && (e.key === "Enter" || e.key === " ")) inputRef.current?.click();
        }}
      >
        {file ? (
          <div className="dropzone-chip">
            <span className="filename">{file.name}</span>
            <span className="filesize">{formatBytes(file.size)}</span>
            {!disabled && (
              <button type="button" className="dropzone-clear" onClick={handleClear} aria-label={`Clear ${label}`}>
                ✕
              </button>
            )}
          </div>
        ) : (
          <span className="dropzone-hint">{hint ?? "Drop a file here, or click to browse"}</span>
        )}
        <input
          ref={inputRef}
          type="file"
          id={id}
          name={name}
          accept={accept}
          required={required}
          disabled={disabled}
          onChange={(e) => setFile(e.currentTarget.files?.[0] ?? null)}
          // Visually hidden but still focusable/operable directly (tab to
          // it, use the OS file picker's native keyboard flow) — the
          // dropzone div above is a convenience layer, not a replacement.
          style={{
            position: "absolute",
            width: 1,
            height: 1,
            padding: 0,
            margin: -1,
            overflow: "hidden",
            clip: "rect(0,0,0,0)",
            whiteSpace: "nowrap",
            border: 0,
          }}
        />
      </div>
    </div>
  );
}

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
  /** Accept several files, kept as an ordered list (order = order sent to
   * the API, which matters for audio/decks — they're concatenated in it). */
  multiple?: boolean;
}

/** A styled drag-and-drop wrapper around a real `<input type="file">` — the
 * input stays in the DOM (keyboard/screen-reader accessible, and what
 * `FormData(form)` actually reads on submit), this just makes it easier to
 * use: click-to-browse, drag-and-drop (including a file dragged in from
 * another tab, e.g. Chrome's downloads shelf), and a compact "chosen file"
 * list with remove (and, in `multiple` mode, reorder) buttons instead of the
 * raw native file input UI. React state is the source of truth for the file
 * list; it's mirrored back into `input.files` on every change so FormData
 * sees the same files, in the same order. */
export function Dropzone({ id, name, label, accept, required, disabled, hint, multiple }: DropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);

  function update(next: File[]) {
    setFiles(next);
    if (inputRef.current) {
      const dt = new DataTransfer();
      next.forEach((f) => dt.items.add(f));
      inputRef.current.files = dt.files;
    }
  }

  function add(incoming: File[]) {
    if (incoming.length === 0) return;
    update(multiple ? [...files, ...incoming] : incoming.slice(0, 1));
  }

  function handleDrop(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    if (disabled) return;
    add(Array.from(event.dataTransfer.files ?? []));
  }

  function remove(index: number, event: React.MouseEvent) {
    event.stopPropagation();
    update(files.filter((_, i) => i !== index));
  }

  function move(index: number, delta: number, event: React.MouseEvent) {
    event.stopPropagation();
    const target = index + delta;
    if (target < 0 || target >= files.length) return;
    const next = [...files];
    [next[index], next[target]] = [next[target], next[index]];
    update(next);
  }

  const emptyHint = hint ?? (multiple ? "Drop files here, or click to browse" : "Drop a file here, or click to browse");

  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <div
        className={`dropzone${dragging ? " dragging" : ""}${disabled ? " disabled" : ""}${files.length ? " has-files" : ""}`}
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
          if (e.target === e.currentTarget && !disabled && (e.key === "Enter" || e.key === " ")) {
            inputRef.current?.click();
          }
        }}
      >
        {files.length > 0 ? (
          <div className="dropzone-list">
            {files.map((file, i) => (
              <div className="dropzone-chip" key={`${file.name}-${file.size}-${i}`}>
                {multiple && files.length > 1 && <span className="dropzone-index">{i + 1}.</span>}
                <span className="filename">{file.name}</span>
                <span className="filesize">{formatBytes(file.size)}</span>
                {!disabled && multiple && files.length > 1 && (
                  <>
                    <button
                      type="button"
                      className="dropzone-clear"
                      onClick={(e) => move(i, -1, e)}
                      disabled={i === 0}
                      aria-label={`Move ${file.name} up`}
                    >
                      ↑
                    </button>
                    <button
                      type="button"
                      className="dropzone-clear"
                      onClick={(e) => move(i, 1, e)}
                      disabled={i === files.length - 1}
                      aria-label={`Move ${file.name} down`}
                    >
                      ↓
                    </button>
                  </>
                )}
                {!disabled && (
                  <button type="button" className="dropzone-clear" onClick={(e) => remove(i, e)} aria-label={`Remove ${file.name}`}>
                    ✕
                  </button>
                )}
              </div>
            ))}
            {multiple && !disabled && <span className="dropzone-hint">Drop or click to add more</span>}
          </div>
        ) : (
          <span className="dropzone-hint">{emptyHint}</span>
        )}
        <input
          ref={inputRef}
          type="file"
          id={id}
          name={name}
          accept={accept}
          multiple={multiple}
          required={required}
          disabled={disabled}
          onChange={(e) => {
            const picked = Array.from(e.currentTarget.files ?? []);
            // Picker replaces input.files with just this pick; `add`
            // re-merges with what's already listed and writes it back.
            add(picked);
          }}
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

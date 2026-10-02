import React, { useEffect, useState } from "react";

/** A titled group of rows, like a card in Claude / Codex settings. */
export function Group({ title, description, children, action }: { title?: string; description?: string; children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <section className="st-group">
      {title || action ? (
        <header className="st-group-head">
          <div>
            {title ? <h3>{title}</h3> : null}
            {description ? <p>{description}</p> : null}
          </div>
          {action}
        </header>
      ) : null}
      <div className="st-card">{children}</div>
    </section>
  );
}

/** One setting: label + help on the left, control on the right. */
export function Row({
  label,
  help,
  children,
  stack = false,
  disabled = false,
}: {
  label: React.ReactNode;
  help?: React.ReactNode;
  children?: React.ReactNode;
  stack?: boolean;
  disabled?: boolean;
}) {
  return (
    <div className={`st-row${stack ? " is-stack" : ""}${disabled ? " is-disabled" : ""}`}>
      <div className="st-row-text">
        <div className="st-row-label">{label}</div>
        {help ? <div className="st-row-help">{help}</div> : null}
      </div>
      {children ? <div className="st-row-control">{children}</div> : null}
    </div>
  );
}

export function Toggle({ checked, onChange, disabled, label }: { checked: boolean; onChange(v: boolean): void; disabled?: boolean; label?: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      className="st-switch"
      data-on={checked ? "true" : "false"}
      disabled={disabled}
      onClick={() => onChange(!checked)}
    >
      <span className="st-switch-knob" />
    </button>
  );
}

export function Select({
  value,
  options,
  onChange,
  disabled,
  wide,
}: {
  value: string;
  options: { value: string; label: string }[];
  onChange(v: string): void;
  disabled?: boolean;
  wide?: boolean;
}) {
  const known = options.some((o) => o.value === value);
  return (
    <div className={`st-select${wide ? " is-wide" : ""}`}>
      <select value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
        {!known && value ? <option value={value}>{value}</option> : null}
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
        <path d="M6 9l6 6 6-6" />
      </svg>
    </div>
  );
}

/** Text field that saves on blur or Enter, not on every keystroke. */
export function TextField({
  value,
  onCommit,
  placeholder,
  type = "text",
  wide,
  mono,
  disabled,
}: {
  value: string | number;
  onCommit(v: string): void;
  placeholder?: string;
  type?: "text" | "number" | "password" | "url";
  wide?: boolean;
  mono?: boolean;
  disabled?: boolean;
}) {
  const [draft, setDraft] = useState(String(value ?? ""));
  useEffect(() => setDraft(String(value ?? "")), [value]);
  const commit = () => {
    if (draft !== String(value ?? "")) onCommit(draft);
  };
  return (
    <input
      className={`st-input${wide ? " is-wide" : ""}${mono ? " is-mono" : ""}`}
      type={type}
      value={draft}
      placeholder={placeholder}
      disabled={disabled}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        if (e.key === "Escape") setDraft(String(value ?? ""));
      }}
    />
  );
}

/** Secret field: never shows the stored value; only sends what you type. */
export function SecretField({ isSet, onCommit, placeholder }: { isSet: boolean; onCommit(v: string): void; placeholder?: string }) {
  const [draft, setDraft] = useState("");
  return (
    <div className="st-secret">
      <input
        className="st-input is-wide is-mono"
        type="password"
        value={draft}
        placeholder={isSet ? "Saved · paste a new one to replace" : placeholder || "Paste key"}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && draft.trim()) {
            onCommit(draft.trim());
            setDraft("");
          }
        }}
      />
      <button
        type="button"
        className="es-btn es-btn-sm"
        disabled={!draft.trim()}
        onClick={() => {
          onCommit(draft.trim());
          setDraft("");
        }}
      >
        Save
      </button>
    </div>
  );
}

export function Segmented({
  value,
  options,
  onChange,
}: {
  value: string;
  options: { value: string; label: string; hint?: string }[];
  onChange(v: string): void;
}) {
  return (
    <div className="st-seg" role="radiogroup">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          className={`st-seg-item${value === o.value ? " is-on" : ""}`}
          onClick={() => onChange(o.value)}
          title={o.hint}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Pick-one cards with a title and explanation (used for big decisions). */
export function ChoiceCards({
  value,
  options,
  onChange,
}: {
  value: string;
  options: { value: string; title: string; body: string; badge?: string }[];
  onChange(v: string): void;
}) {
  return (
    <div className="st-choices">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          className={`st-choice${value === o.value ? " is-on" : ""}`}
          aria-pressed={value === o.value}
          onClick={() => onChange(o.value)}
        >
          <span className="st-choice-radio" aria-hidden />
          <span className="st-choice-text">
            <strong>
              {o.title}
              {o.badge ? <em>{o.badge}</em> : null}
            </strong>
            <small>{o.body}</small>
          </span>
        </button>
      ))}
    </div>
  );
}

export function Status({ tone, children }: { tone: "ok" | "warn" | "err" | "idle"; children: React.ReactNode }) {
  return (
    <span className="st-status" data-tone={tone}>
      <i />
      {children}
    </span>
  );
}

/** Editable list of strings (folders, user ids, domains). */
export function ListEditor({ items, onChange, placeholder, mono }: { items: string[]; onChange(items: string[]): void; placeholder?: string; mono?: boolean }) {
  const [draft, setDraft] = useState("");
  const add = () => {
    const value = draft.trim();
    if (!value || items.includes(value)) return;
    onChange([...items, value]);
    setDraft("");
  };
  return (
    <div className="st-list">
      {items.map((item) => (
        <div key={item} className="st-list-item">
          <span className={mono ? "is-mono" : undefined}>{item}</span>
          <button type="button" className="es-icon-btn" aria-label={`Remove ${item}`} onClick={() => onChange(items.filter((x) => x !== item))}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
          </button>
        </div>
      ))}
      <div className="st-list-add">
        <input
          className={`st-input is-wide${mono ? " is-mono" : ""}`}
          value={draft}
          placeholder={placeholder}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && add()}
        />
        <button type="button" className="es-btn es-btn-sm" onClick={add} disabled={!draft.trim()}>
          Add
        </button>
      </div>
    </div>
  );
}

import React from "react";
import { useTheme, type ThemeChoice } from "./theme";

/** Light, Dark or Match system, each with a small preview. Used in Settings and in setup. */
const OPTIONS: { value: ThemeChoice; label: string; hint: string }[] = [
  { value: "light", label: "Light", hint: "Blueprint white" },
  { value: "dark", label: "Dark", hint: "Classic black" },
  { value: "system", label: "Match system", hint: "Follows Windows" },
];

function Preview({ tone }: { tone: ThemeChoice }) {
  return (
    <span className={`es-theme-preview is-${tone}`} aria-hidden>
      <span className="es-theme-preview-side"><i /><i /><i /></span>
      <span className="es-theme-preview-main">
        <span className="es-theme-preview-faces"><span className="es-theme-preview-face"><b /><b /></span><span className="es-theme-preview-face is-mate"><b /><b /></span></span>
        <span className="es-theme-preview-line" />
        <span className="es-theme-preview-line is-short" />
        <span className="es-theme-preview-bubble" />
      </span>
    </span>
  );
}

export function ThemePicker({ label = "Appearance" }: { label?: string }) {
  const { choice, setChoice } = useTheme();
  return (
    <div className="es-theme-picker" role="radiogroup" aria-label={label}>
      {OPTIONS.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={choice === option.value}
          className={`es-theme-option${choice === option.value ? " is-on" : ""}`}
          onClick={() => setChoice(option.value)}
        >
          <Preview tone={option.value} />
          <span className="es-theme-option-text">
            <strong>{option.label}</strong>
            <small>{option.hint}</small>
          </span>
        </button>
      ))}
    </div>
  );
}

import { Fragment } from "react";

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Render `text`, wrapping every (case-insensitive) occurrence of any string in
 * `snippets` in a highlighted `<mark>`. Used to point at the exact conflicting
 * phrases the conflict detector quoted (2e). No snippets ⇒ plain text.
 *
 * Snippets are matched verbatim (the backend already dropped any the model did
 * not quote exactly), so a captured segment is a real match iff it equals one
 * of the terms — which is how we tell matches apart from the gaps `split`
 * leaves behind.
 */
export function HighlightText({
  text,
  snippets,
  className,
}: {
  text: string;
  snippets: string[];
  className?: string;
}) {
  const terms = Array.from(
    new Set((snippets ?? []).map((s) => s.trim()).filter(Boolean))
  );
  if (terms.length === 0) {
    return <span className={className}>{text}</span>;
  }
  // Longest-first so an overlapping longer phrase wins over a shorter one.
  const pattern = terms
    .slice()
    .sort((a, b) => b.length - a.length)
    .map(escapeRegExp)
    .join("|");
  const lowered = new Set(terms.map((t) => t.toLowerCase()));
  const parts = text.split(new RegExp(`(${pattern})`, "gi"));

  return (
    <span className={className}>
      {parts.map((part, i) =>
        lowered.has(part.toLowerCase()) ? (
          <mark
            key={i}
            style={{
              backgroundColor: "rgba(239, 68, 68, 0.35)",
              color: "var(--text-primary)",
              borderRadius: "3px",
              padding: "0 2px",
            }}
          >
            {part}
          </mark>
        ) : (
          <Fragment key={i}>{part}</Fragment>
        )
      )}
    </span>
  );
}

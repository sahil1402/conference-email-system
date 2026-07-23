"use client";

import { useLayoutEffect, useRef } from "react";

import { cn } from "@/lib/utils";

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Box-model classes shared EXACTLY by the textarea and its highlight backdrop.
// Any divergence (padding, border, font, line-height, wrapping) would misalign
// the marks from the characters the user actually types.
const BOX = "w-full rounded-lg border px-3 py-2 text-sm leading-relaxed";

/**
 * A textarea whose text you can edit while the given `snippets` stay
 * highlighted in place (2e) — so a chair sees exactly where the conflicting
 * passage sits in the box they type in. A textarea can't render marks itself,
 * so a mirrored backdrop draws the highlights behind a transparent-background
 * textarea sharing the same box model.
 *
 * The textarea AUTO-SIZES to its content so it never shows its own scrollbar —
 * a scrollbar would narrow the textarea's wrap width relative to the backdrop
 * and walk the marks off the characters. No scroll ⇒ identical wrapping ⇒ the
 * highlights stay aligned.
 */
export function HighlightTextarea({
  value,
  onChange,
  snippets,
  minRows = 4,
}: {
  value: string;
  onChange: (v: string) => void;
  snippets: string[];
  minRows?: number;
}) {
  const taRef = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    // Add the border (offset − client, no scrollbar since overflow is hidden)
    // so a border-box textarea fits content exactly without a 1–2px scroll.
    const borderY = ta.offsetHeight - ta.clientHeight;
    ta.style.height = `${ta.scrollHeight + borderY}px`;
  }, [value]);

  const terms = Array.from(new Set(snippets.map((s) => s.trim()).filter(Boolean)));
  const lowered = new Set(terms.map((t) => t.toLowerCase()));
  const parts =
    terms.length > 0
      ? value.split(
          new RegExp(
            `(${terms
              .slice()
              .sort((a, b) => b.length - a.length)
              .map(escapeRegExp)
              .join("|")})`,
            "gi"
          )
        )
      : [value];

  return (
    <div className="relative">
      {/* Backdrop: same box + text, transparent glyphs, only the marks visible. */}
      <div
        aria-hidden
        className={cn(
          BOX,
          "pointer-events-none absolute inset-0 overflow-hidden whitespace-pre-wrap [overflow-wrap:break-word]"
        )}
        style={{
          backgroundColor: "var(--surface)",
          borderColor: "transparent",
          color: "transparent",
        }}
      >
        {parts.map((p, i) =>
          lowered.has(p.toLowerCase()) ? (
            <mark
              key={i}
              style={{
                // A saturated translucent red reads clearly behind the text,
                // in both themes, without hiding it (var(--danger-subtle) was
                // too faint under the textarea glyphs).
                backgroundColor: "rgba(239, 68, 68, 0.35)",
                color: "transparent",
                borderRadius: "2px",
              }}
            >
              {p}
            </mark>
          ) : (
            <span key={i}>{p}</span>
          )
        )}
        {"\n"}
      </div>
      <textarea
        ref={taRef}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={cn(
          BOX,
          "relative block resize-none overflow-hidden whitespace-pre-wrap bg-transparent outline-none transition-colors focus:border-[var(--accent)]"
        )}
        style={{
          borderColor: "var(--border)",
          color: "var(--text-primary)",
          minHeight: `${minRows * 1.625}em`,
        }}
      />
    </div>
  );
}

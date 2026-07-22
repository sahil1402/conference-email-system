import type { ReactNode } from "react";

/**
 * A single keyboard key rendered as a small key-cap. Compose several side by
 * side for a combo (e.g. <Kbd>Ctrl</Kbd> <Kbd>Alt</Kbd> <Kbd>S</Kbd>).
 * Purely presentational and token-driven, so it reads correctly in both themes.
 */
export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd
      className="inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded border px-1 font-mono text-[11px]"
      style={{
        backgroundColor: "var(--surface-raised)",
        borderColor: "var(--border)",
        color: "var(--text-secondary)",
      }}
    >
      {children}
    </kbd>
  );
}

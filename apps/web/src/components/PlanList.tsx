import type { Progress } from "../types/schemas";

const MARK = { completed: "●", in_progress: "◐", pending: "○" } as const;

/** The orchestrator's plan (write_todos) as a compact checklist. */
export function PlanList({ progress }: { progress: Progress | null }) {
  if (!progress || progress.todos.length === 0) return null;
  return (
    <ul className="num border-b border-line px-3 py-2 text-[12px]">
      {progress.todos.map((t, i) => (
        <li key={i} className={t.status === "completed" ? "text-dim" : t.status === "in_progress" ? "text-accent" : "text-faint"}>
          <span aria-hidden className="mr-2">{MARK[t.status]}</span>
          {t.content}
        </li>
      ))}
    </ul>
  );
}

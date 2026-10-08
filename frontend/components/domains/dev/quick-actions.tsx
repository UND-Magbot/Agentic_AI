export function DevQuickActions({ onPick }: { onPick: (text: string) => void }) {
  const actions = ["오늘 머지된 PR 요약", "실패한 CI 분석", "릴리스 노트 초안"];
  return (
    <div className="flex flex-wrap gap-2">
      {actions.map((a) => (
        <button
          key={a}
          type="button"
          onClick={() => onPick(a)}
          className="rounded-full border border-border bg-surface px-3 py-1 text-xs text-foreground-muted hover:text-foreground"
        >
          {a}
        </button>
      ))}
    </div>
  );
}

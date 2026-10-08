export function DesignQuickActions({ onPick }: { onPick: (text: string) => void }) {
  const actions = ["최근 ECO 영향 분석", "BOM 단가 변동 점검", "사양서 누락 항목 체크"];
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

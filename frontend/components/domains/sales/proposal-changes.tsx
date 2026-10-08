// 수정 요청 한 번으로 컨셉 구성에서 바뀐 점 — 계획 단계·이미지 단계 공용.
import type { ConceptChanges } from "@/lib/shared/proposal-projects";

/** 마지막 수정으로 구성에서 바뀐 점 — 반영이 실제로 됐는지 한눈에. */
export function ChangesBox({ changes, planning = false }: { changes: ConceptChanges; planning?: boolean }) {
  const same = !changes.added.length && !changes.removed.length && !changes.robot && !changes.name;
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-accent/25 bg-accent-soft/20 px-3 py-2 text-[12.5px]">
      <div className="font-semibold text-foreground">마지막 수정으로 바뀐 점</div>
      {changes.note && <p className="text-foreground-muted">{changes.note}</p>}
      {same ? (
        <p className="text-amber-700 dark:text-amber-300">
          {planning
            ? "구성 문장은 그대로입니다. 바꿀 장치·위치를 더 구체적으로 적어 보세요."
            : "구성 문장은 그대로입니다 — 그림에 들어가는 구성이 바뀌지 않아 새 그림을 그리지 않았습니다. 더 구체적으로 요청해 보세요."}
        </p>
      ) : (
        <ul className="flex flex-col gap-0.5">
          {changes.name && <li className="text-foreground">이름: {changes.name[0]} → <b>{changes.name[1]}</b></li>}
          {changes.robot && <li className="text-foreground">로봇: {changes.robot[0]} → <b>{changes.robot[1]}</b></li>}
          {changes.added.map((x, i) => (
            <li key={`a${i}`} className="text-emerald-700 dark:text-emerald-300">+ {x}</li>
          ))}
          {changes.removed.map((x, i) => (
            <li key={`r${i}`} className="text-foreground-subtle line-through">− {x}</li>
          ))}
          {changes.excluded?.map((x, i) => (
            <li key={`e${i}`} className="text-foreground-muted">그림에서 뺌: {x}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

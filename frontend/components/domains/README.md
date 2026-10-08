# components/domains

도메인 전용 UI 컴포넌트의 자리.

- 한 도메인의 UI 위젯 (예: 기술영업의 파이프라인 카드, 기구설계의 BOM 트리, 재무관리의 원장 테이블)은 이 폴더 아래 `<domain>/` 에 둔다.
- 도메인 간 직접 import 금지. 다른 도메인 컴포넌트가 필요하면 `components/shared/` 로 승격시킨다.
- shared 셸(ChatWindow 등)은 `domain` 메타를 props 로 받아서 동작하므로, 도메인 위젯은 shared 셸에 주입(slot)되는 방식으로 결합한다.

지금 단계에서는 각 도메인에 placeholder `quick-actions.tsx` 만 두어 패턴을 고정한다. 실 위젯은 백엔드 도구(tools.ts)와 함께 진화시킨다.

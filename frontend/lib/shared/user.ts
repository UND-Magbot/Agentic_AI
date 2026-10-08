/**
 * 임시 사용자명 소스.
 *  - 인증 도입 전까지 환경변수 NEXT_PUBLIC_USER_NAME 한 곳에서만 가져온다(SSR/CSR 동일).
 *  - 인증 도입 후엔 이 함수 시그니처를 유지한 채 내부만 세션 사용자로 교체한다.
 */
export function getUserName(): string {
  const fromEnv = process.env.NEXT_PUBLIC_USER_NAME;
  if (fromEnv && fromEnv.trim()) return fromEnv.trim();
  return "UND";
}

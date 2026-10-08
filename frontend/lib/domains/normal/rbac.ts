import type { DomainPolicy } from "@/lib/core/auth/rbac";

// 일반 도메인은 사내 민감 데이터에 접근하지 않으므로 모든 역할에 read 만 허용한다.
export const policy: DomainPolicy = {
  admin: ["read"],
  manager: ["read"],
  staff: ["read"],
  guest: ["read"],
};

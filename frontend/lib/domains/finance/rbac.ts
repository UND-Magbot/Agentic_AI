import type { DomainPolicy } from "@/lib/core/auth/rbac";

export const policy: DomainPolicy = {
  admin: ["read", "write", "approve"],
  manager: ["read", "write"],
  staff: ["read"],
  // guest: 명시 안 함 = 권한 없음
};

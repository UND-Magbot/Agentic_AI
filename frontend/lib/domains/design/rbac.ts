import type { DomainPolicy } from "@/lib/core/auth/rbac";

export const policy: DomainPolicy = {
  admin: ["read", "write", "approve"],
  manager: ["read", "write", "approve"],
  staff: ["read", "write"],
  // guest 권한 없음 — 기구설계 산출물은 기본 비공개
};

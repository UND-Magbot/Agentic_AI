import type { DomainPolicy } from "@/lib/core/auth/rbac";

export const policy: DomainPolicy = {
  admin: ["read", "write", "approve"],
  manager: ["read", "write", "approve"],
  staff: ["read", "write"],
  guest: ["read"],
};

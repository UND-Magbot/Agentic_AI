// 도메인 무관 RBAC 프리미티브. 도메인별 정책은 lib/domains/<domain>/rbac.ts.

export type Role = "admin" | "manager" | "staff" | "guest";
export type Permission = "read" | "write" | "approve";

export type DomainPolicy = Partial<Record<Role, ReadonlyArray<Permission>>>;

export function can(policy: DomainPolicy, role: Role, perm: Permission): boolean {
  return policy[role]?.includes(perm) ?? false;
}

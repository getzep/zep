/**
 * Map a display name into Zep user fields for better user-node entity mapping.
 */
export function splitDisplayName(userName: string): {
  firstName: string;
  lastName?: string;
} {
  const parts = userName.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return { firstName: "User" };
  if (parts.length === 1) return { firstName: parts[0]! };
  return {
    firstName: parts[0]!,
    lastName: parts.slice(1).join(" "),
  };
}

/** Stable demo email derived from the application user key (helps Zep entity dedup). */
export function demoEmailForUserKey(userKey: string): string {
  const local = userKey.replace(/[^a-zA-Z0-9._+-]/g, "-").slice(0, 64) || "user";
  return `${local}@example.invalid`;
}

import { getZepClient } from "./zep-client";
import type { ZepIdentity } from "./identity";
import { demoEmailForUserId, splitDisplayName } from "./zep-user-fields";

/** Ensure the Zep user exists (no thread). Safe to call repeatedly. */
export async function ensureZepUser(
  userId: string,
  userName: string,
): Promise<void> {
  const zep = getZepClient();
  const { firstName, lastName } = splitDisplayName(userName);

  try {
    await zep.user.add({
      userId,
      firstName,
      ...(lastName ? { lastName } : {}),
      email: demoEmailForUserId(userId),
    });
  } catch (error) {
    if (!isAlreadyExists(error)) {
      try {
        await zep.user.get(userId);
      } catch {
        throw error;
      }
    }
  }
}

/**
 * Ensure the Zep user + thread exist. Safe to call repeatedly.
 * Swallows only known "already exists" conflicts so hooks/tools stay idempotent.
 */
export async function ensureZepUserAndThread(
  identity: ZepIdentity,
): Promise<void> {
  await ensureZepUser(identity.userId, identity.userName);
  const zep = getZepClient();

  try {
    await zep.thread.create({
      threadId: identity.threadId,
      userId: identity.userId,
    });
  } catch (error) {
    if (!isAlreadyExists(error)) {
      try {
        await zep.thread.get(identity.threadId);
      } catch {
        throw error;
      }
    }
  }
}

function isAlreadyExists(error: unknown): boolean {
  const status =
    typeof error === "object" &&
    error !== null &&
    "statusCode" in error &&
    typeof (error as { statusCode: unknown }).statusCode === "number"
      ? (error as { statusCode: number }).statusCode
      : undefined;

  // Conflict is unambiguous. Do not treat every 400 as "already exists" —
  // validation failures are also 400s and must surface.
  if (status === 409) return true;

  const message =
    error instanceof Error
      ? error.message
      : typeof error === "string"
        ? error
        : "";
  return /already exists|duplicate|conflict/i.test(message);
}

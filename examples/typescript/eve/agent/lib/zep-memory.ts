import { getZepClient } from "./zep-client";
import type { ZepIdentity } from "./identity";
import { demoEmailForUserKey, splitDisplayName } from "./zep-user-fields";

export interface ZepUserRefs {
  userUuid: string;
  graphUuid: string;
}

export interface ZepThreadRefs extends ZepUserRefs {
  threadUuid: string;
}

/**
 * v4 addresses a user, a graph, and a thread by a server-generated UUID. A
 * real application stores the UUID in its own database next to the application
 * key. This demo keeps the map in process, because the utterance stash is also
 * in process.
 */
const userRefsByKey = new Map<string, ZepUserRefs>();
const threadUuidBySessionKey = new Map<string, string>();

/** Ensure the Zep user exists (no thread). Safe to call repeatedly. */
export async function ensureZepUser(
  userKey: string,
  userName: string,
): Promise<ZepUserRefs> {
  const cached = userRefsByKey.get(userKey);
  if (cached) return cached;

  const zep = getZepClient();
  const { firstName, lastName } = splitDisplayName(userName);

  const user = await zep.user.create({
    firstName,
    ...(lastName ? { lastName } : {}),
    email: demoEmailForUserKey(userKey),
  });

  if (!user.uuid || !user.graphUuid) {
    throw new Error("Zep did not return a user UUID and a graph UUID");
  }

  const refs: ZepUserRefs = { userUuid: user.uuid, graphUuid: user.graphUuid };
  userRefsByKey.set(userKey, refs);
  return refs;
}

/**
 * Ensure the Zep user and thread exist. Safe to call repeatedly, because the
 * UUIDs of the first call stay in the process map.
 */
export async function ensureZepUserAndThread(
  identity: ZepIdentity,
): Promise<ZepThreadRefs> {
  const userRefs = await ensureZepUser(identity.userKey, identity.userName);

  const cachedThreadUuid = threadUuidBySessionKey.get(identity.sessionKey);
  if (cachedThreadUuid) {
    return { ...userRefs, threadUuid: cachedThreadUuid };
  }

  const zep = getZepClient();
  const thread = await zep.thread.create({ userUuid: userRefs.userUuid });
  if (!thread.uuid) {
    throw new Error("Zep did not return a thread UUID");
  }

  threadUuidBySessionKey.set(identity.sessionKey, thread.uuid);
  return { ...userRefs, threadUuid: thread.uuid };
}

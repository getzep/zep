import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { getZepClient } from "./zep-client";
import type { ZepIdentity } from "./identity";
import { demoEmailForUserId, splitDisplayName } from "./zep-user-fields";

interface IdentityStore {
  users: Record<string, string>;
  threads: Record<string, string>;
}

const identityPath =
  process.env.ZEP_IDENTITY_FILE?.trim() ||
  join(process.cwd(), ".data", "zep-identities.json");
let identityStorePromise: Promise<IdentityStore> | undefined;

async function getIdentityStore(): Promise<IdentityStore> {
  identityStorePromise ??= readFile(identityPath, "utf8")
    .then((contents) => JSON.parse(contents) as IdentityStore)
    .catch((error: unknown) => {
      if (
        typeof error === "object" &&
        error !== null &&
        "code" in error &&
        error.code === "ENOENT"
      ) {
        return { users: {}, threads: {} };
      }
      throw error;
    });
  return identityStorePromise;
}

async function saveIdentityStore(store: IdentityStore): Promise<void> {
  await mkdir(dirname(identityPath), { recursive: true });
  await writeFile(identityPath, JSON.stringify(store, null, 2));
}

export async function ensureZepUser(
  userId: string,
  userName: string,
): Promise<string> {
  const zep = getZepClient();
  const store = await getIdentityStore();
  const existingUuid = store.users[userId];
  if (existingUuid) return existingUuid;

  const { firstName, lastName } = splitDisplayName(userName);
  const user = await zep.user.create({
    userId,
    firstName,
    ...(lastName ? { lastName } : {}),
    email: demoEmailForUserId(userId),
  });
  if (!user.uuid) throw new Error("Zep did not return a user UUID");
  store.users[userId] = user.uuid;
  await saveIdentityStore(store);
  return user.uuid;
}

export async function ensureZepUserAndThread(
  identity: ZepIdentity,
): Promise<{ userUuid: string; threadUuid: string }> {
  const userUuid = await ensureZepUser(identity.userId, identity.userName);
  const zep = getZepClient();
  const store = await getIdentityStore();
  let threadUuid = store.threads[identity.threadId];
  if (!threadUuid) {
    const thread = await zep.thread.create({ userUuid });
    if (!thread.uuid) throw new Error("Zep did not return a thread UUID");
    threadUuid = thread.uuid;
    store.threads[identity.threadId] = threadUuid;
    await saveIdentityStore(store);
  }
  return { userUuid, threadUuid };
}

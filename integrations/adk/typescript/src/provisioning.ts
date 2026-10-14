/**
 * Create Zep users and threads before an ADK agent runs.
 */

import type { ZepClient } from "@getzep/zep-cloud";

export type UserSetupHook = (
  zep: ZepClient,
  userUuid: string,
) => Promise<void>;

export interface CreateUserOptions {
  firstName?: string;
  lastName?: string;
  email?: string;
  onCreated?: UserSetupHook;
}

export interface CreatedUser {
  userUuid: string;
  graphUuid: string;
}

export interface CreateThreadOptions {
  userUuid: string;
}

export interface CreatedThread {
  threadUuid: string;
  graphUuid: string;
}

function requireUuid(value: string | undefined, resource: string): string {
  if (!value) {
    throw new Error(`Zep did not return a UUID for the created ${resource}.`);
  }
  return value;
}

export async function createUser(
  zep: ZepClient,
  options: CreateUserOptions = {},
): Promise<CreatedUser> {
  const { firstName, lastName, email, onCreated } = options;
  const user = await zep.user.create({ firstName, lastName, email });
  const userUuid = requireUuid(user.uuid, "user");
  const graphUuid = requireUuid(user.graphUuid, "user graph");

  if (onCreated) {
    await onCreated(zep, userUuid);
  }

  return { userUuid, graphUuid };
}

export async function createThread(
  zep: ZepClient,
  options: CreateThreadOptions,
): Promise<CreatedThread> {
  const thread = await zep.thread.create({ userUuid: options.userUuid });
  return {
    threadUuid: requireUuid(thread.uuid, "thread"),
    graphUuid: requireUuid(thread.graphUuid, "thread graph"),
  };
}

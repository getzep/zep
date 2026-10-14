import type { Content } from "@google/genai";
import { ZepIdentityError } from "./errors.js";

export const STATE_KEYS = {
  userUuid: "zep_user_uuid",
  threadUuid: "zep_thread_uuid",
  graphUuid: "zep_graph_uuid",
  firstName: "zep_first_name",
  lastName: "zep_last_name",
} as const;

export interface ZepIdentityOptions {
  userUuid?: string;
  threadUuid?: string;
  firstName?: string;
  lastName?: string;
}

export interface ResolvedIdentity {
  userUuid: string;
  threadUuid: string;
  firstName?: string;
  lastName?: string;
  displayName?: string;
}

export interface AdkContextLike {
  readonly userId: string;
  readonly sessionId: string;
  readonly invocationId: string;
  readonly userContent?: Content;
  readonly state: { get<T>(key: string, defaultValue?: T): T | undefined };
}

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isZepUuid(value: unknown): value is string {
  return typeof value === "string" && UUID_PATTERN.test(value);
}

function readStateString(context: AdkContextLike, key: string): string | undefined {
  try {
    const value = context.state.get<unknown>(key);
    return typeof value === "string" && value.length > 0 ? value : undefined;
  } catch {
    return undefined;
  }
}

function readStateUuid(
  context: AdkContextLike,
  key: string,
): string | undefined {
  const value = readStateString(context, key);
  return isZepUuid(value) ? value : undefined;
}

export function resolveUserUuid(
  context: AdkContextLike,
  options: ZepIdentityOptions = {},
): string {
  const userUuid =
    options.userUuid ??
    readStateUuid(context, STATE_KEYS.userUuid) ??
    (isZepUuid(context.userId) ? context.userId : undefined);
  if (!userUuid) {
    throw new ZepIdentityError(
      "Cannot resolve a Zep user UUID. Pass `userUuid`, set " +
        `'${STATE_KEYS.userUuid}' in session state, or use an ADK userId that is a UUID.`,
    );
  }
  return userUuid;
}

export function resolveIdentity(
  context: AdkContextLike,
  options: ZepIdentityOptions = {},
): ResolvedIdentity {
  const userUuid = resolveUserUuid(context, options);
  const threadUuid =
    options.threadUuid ??
    readStateUuid(context, STATE_KEYS.threadUuid) ??
    (isZepUuid(context.sessionId) ? context.sessionId : undefined);
  if (!threadUuid) {
    throw new ZepIdentityError(
      "Cannot resolve a Zep thread UUID. Pass `threadUuid`, set " +
        `'${STATE_KEYS.threadUuid}' in session state, or use an ADK sessionId that is a UUID.`,
    );
  }

  const firstName =
    options.firstName ?? readStateString(context, STATE_KEYS.firstName);
  const lastName =
    options.lastName ?? readStateString(context, STATE_KEYS.lastName);
  const displayName = [firstName, lastName]
    .filter((part): part is string => Boolean(part))
    .join(" ")
    .trim();

  return {
    userUuid,
    threadUuid,
    firstName,
    lastName,
    displayName: displayName.length > 0 ? displayName : undefined,
  };
}

export function extractText(content: Content | undefined): string | undefined {
  const parts = content?.parts;
  if (!parts || parts.length === 0) {
    return undefined;
  }
  const text = parts
    .map((part) => part.text)
    .filter((value): value is string => Boolean(value))
    .join(" ")
    .trim();
  return text.length > 0 ? text : undefined;
}

import type { SessionAuth } from "eve/context";

export interface ZepIdentity {
  /** Zep user_id — never accept this from the model. */
  userId: string;
  /** Display name for Zep message `name` fields. */
  userName: string;
  /** Zep thread_id — mapped from the Eve session. */
  threadId: string;
}

/** Minimal principal fields used for Zep user mapping. */
export interface ZepCallerLike {
  readonly principalType?: string;
  readonly principalId?: string;
  readonly attributes?: Readonly<Record<string, unknown>>;
}

/** Minimal session shape shared by tools, hooks, and dynamic resolvers. */
export interface IdentitySessionContext {
  readonly session: {
    readonly id: string;
    readonly auth: SessionAuth;
  };
}

function displayNameFromCaller(
  caller: ZepCallerLike | null | undefined,
  fallback: string,
): string {
  const attrName = caller?.attributes?.name;
  if (typeof attrName === "string" && attrName.trim().length > 0) {
    return attrName.trim();
  }
  if (Array.isArray(attrName) && typeof attrName[0] === "string") {
    return attrName[0];
  }
  return fallback;
}

/**
 * Shared userId / userName resolution for channel onMessage, hooks, and
 * dynamic instruction resolvers.
 * Returns null when there is not yet a stable id (create-session without
 * ZEP_DEMO_USER_ID / authenticated user).
 */
export function resolveZepUserFields(options: {
  caller?: ZepCallerLike | null;
  /** Existing Eve session id, when known. */
  sessionId?: string | null;
}): { userId: string; userName: string } | null {
  const envUserId = process.env.ZEP_DEMO_USER_ID?.trim();
  const envUserName = process.env.ZEP_DEMO_USER_NAME?.trim() || "Demo User";
  const caller = options.caller;

  const userId =
    (caller?.principalType === "user" && caller.principalId) ||
    envUserId ||
    (options.sessionId ? `eve-session-${options.sessionId}` : null);

  if (!userId) return null;

  return {
    userId,
    userName: displayNameFromCaller(caller, envUserName),
  };
}

/**
 * Resolve Zep identity from Eve session auth when present.
 * Falls back to ZEP_DEMO_USER_ID for local demos without auth.
 */
export function resolveZepIdentity(ctx: IdentitySessionContext): ZepIdentity {
  const caller =
    (ctx.session.auth.current as ZepCallerLike | null | undefined) ??
    (ctx.session.auth.initiator as ZepCallerLike | null | undefined);

  const fields = resolveZepUserFields({
    caller,
    sessionId: ctx.session.id,
  });

  // session.id always exists here, so fields is non-null.
  const { userId, userName } = fields!;

  // One Eve session ↔ one Zep thread keeps conversation continuity clean.
  const threadId = `eve-${ctx.session.id}`;

  return { userId, userName, threadId };
}

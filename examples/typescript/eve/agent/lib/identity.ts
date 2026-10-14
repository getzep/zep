import type { SessionAuth } from "eve/context";

export interface ZepIdentity {
  /**
   * Stable application key for the person. v4 addresses a Zep user by a
   * server-generated UUID, so the application maps its own key to that UUID.
   * Never accept this key from the model.
   */
  userKey: string;
  /** Display name for Zep message `name` fields. */
  userName: string;
  /** Application key of the Eve session, mapped to one Zep thread UUID. */
  sessionKey: string;
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
 * Shared user key / userName resolution for channel onMessage, hooks, and
 * dynamic instruction resolvers.
 * Returns null when there is not yet a stable key (create-session without
 * ZEP_DEMO_USER_KEY / authenticated user).
 */
export function resolveZepUserFields(options: {
  caller?: ZepCallerLike | null;
  /** Existing Eve session id, when known. */
  sessionId?: string | null;
}): { userKey: string; userName: string } | null {
  const envUserKey = process.env.ZEP_DEMO_USER_KEY?.trim();
  const envUserName = process.env.ZEP_DEMO_USER_NAME?.trim() || "Demo User";
  const caller = options.caller;

  const userKey =
    (caller?.principalType === "user" && caller.principalId) ||
    envUserKey ||
    (options.sessionId ? `eve-session-${options.sessionId}` : null);

  if (!userKey) return null;

  return {
    userKey,
    userName: displayNameFromCaller(caller, envUserName),
  };
}

/**
 * Resolve Zep identity from Eve session auth when present.
 * Falls back to ZEP_DEMO_USER_KEY for local demos without auth.
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
  const { userKey, userName } = fields!;

  // One Eve session ↔ one Zep thread keeps conversation continuity clean.
  const sessionKey = `eve-${ctx.session.id}`;

  return { userKey, userName, sessionKey };
}

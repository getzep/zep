import type { Zep } from "@getzep/zep-cloud";

/** A binding to a user graph or a shared graph. */
export interface ZepBinding {
  /** The UUID of the user graph or shared graph. */
  graphUuid?: string;
}

/** A graph binding with a conversation thread. */
export interface ZepThreadBinding extends ZepBinding {
  /** The UUID of the conversation thread. */
  threadUuid: string;
}

/** Logger interface compatible with `console` and structured loggers. */
export interface ZepLogger {
  warn: (message: string, ...args: unknown[]) => void;
  debug?: (message: string, ...args: unknown[]) => void;
}

/** Re-export of Zep's role enum. */
export type RoleType = Zep.RoleType;

/** Resolved UUIDs for a processor or tool call. */
export interface ResolvedZepIdentity {
  graphUuid?: string;
  threadUuid?: string;
}

/** Resolve UUIDs from Mastra's per-call `requestContext`. */
export type ZepIdentityResolver = (
  requestContext: unknown,
) => ResolvedZepIdentity | undefined | Promise<ResolvedZepIdentity | undefined>;

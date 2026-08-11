import type { UserContent } from "ai";
import {
  eveChannel,
  defaultEveAuth,
  type EveMessageContext,
} from "eve/channels/eve";
import { localDev, placeholderAuth, vercelOidc } from "eve/channels/auth";
import { resolveZepUserFields } from "../lib/identity";
import { flattenUserContent } from "../lib/message-text";
import { stashPendingUtterance } from "../lib/pending-utterance";

/**
 * Stash the inbound utterance for turn-relevant Zep recall.
 *
 * Eve resolves dynamic instructions on `turn.started` *before*
 * `message.received`, and that resolver does not receive the user text.
 * `onMessage` runs after the HTTP body is parsed, so we record the text
 * here; `agent/instructions/zep-memory.ts` consumes it on `turn.started`.
 *
 * Do **not** return `context` here — those strings enter durable session
 * history and accumulate. Memory is injected via turn-scoped system
 * instructions instead (replaced each turn).
 */
export default eveChannel({
  auth: [
    // Lets the eve TUI and your Vercel deployments reach the deployed agent.
    vercelOidc(),
    // Open on localhost for `eve dev` and the REPL; ignored in production.
    localDev(),
    // Placeholder for browser requests in production demos.
    // Replace with Auth.js / Clerk (or similar) and map principalId → Zep userId.
    placeholderAuth(),
  ],
  async onMessage(ctx: EveMessageContext, message: string | UserContent) {
    const auth = defaultEveAuth(ctx);
    const text = flattenUserContent(message);
    if (!text) return { auth };

    const identity = resolveZepUserFields({
      caller: ctx.eve.caller,
      sessionId: ctx.eve.sessionId,
    });

    if (!identity && !ctx.eve.sessionId) {
      // localDev principals are not principalType "user"; without
      // ZEP_DEMO_USER_ID the create-session turn has nothing to key the stash.
      console.warn(
        "[zep-channel] cannot stash utterance for recall: set ZEP_DEMO_USER_ID or use authenticated user auth",
      );
      return { auth };
    }

    stashPendingUtterance({
      text,
      sessionId: ctx.eve.sessionId,
      userId: identity?.userId,
    });

    return { auth };
  },
});

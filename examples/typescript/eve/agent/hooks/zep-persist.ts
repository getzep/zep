import { defineHook } from "eve/hooks";
import { resolveZepIdentity } from "../lib/identity";
import { bindPendingUtteranceToSession } from "../lib/pending-utterance";
import { getZepClient } from "../lib/zep-client";
import { ensureZepUserAndThread } from "../lib/zep-memory";

/**
 * Auto-persist Eve conversation turns into Zep.
 *
 * - session.started → provision Zep user/thread; rebind create-session stash
 * - message.received → user message
 * - message.completed (not tool-calls) → assistant reply
 *
 * Turn-relevant recall happens in `agent/instructions/zep-memory.ts`
 * (`turn.started` dynamic instructions, utterance stashed from channel
 * `onMessage`). This hook only writes to Zep; it does not inject model context.
 *
 * Hooks are observe-only and at-least-once. Failures are swallowed so a Zep
 * outage never fails the Eve turn.
 */
export default defineHook({
  events: {
    async "session.started"(_event, ctx) {
      try {
        const identity = resolveZepIdentity(ctx);
        // Create-session onMessage had no sessionId — move that utterance
        // onto this session before turn.started instructions run (hooks for
        // session.started fire before turn.started in the same preamble).
        bindPendingUtteranceToSession({
          sessionId: ctx.session.id,
          userId: identity.userId,
        });
        await ensureZepUserAndThread(identity);
        // Warm is best-effort and must not delay turn.started (stash TTL /
        // first-turn recall). Fire-and-forget after provision.
        void getZepClient()
          .user.warm(identity.userId)
          .catch((error) => {
            console.warn("[zep-persist] user.warm failed", error);
          });
        // Do not log userId — it can come from env (ZEP_DEMO_USER_ID) and
        // CodeQL flags clear-text logging of process environment values.
        console.info("[zep-persist] provisioned", {
          sessionId: ctx.session.id,
        });
      } catch (error) {
        console.error("[zep-persist] session.started failed", error);
      }
    },

    async "message.received"(event, ctx) {
      try {
        const text = event.data.message?.trim();
        if (!text) return;

        const identity = resolveZepIdentity(ctx);
        await ensureZepUserAndThread(identity);

        await getZepClient().thread.addMessages(identity.threadId, {
          messages: [
            {
              role: "user",
              name: identity.userName,
              content: text,
            },
          ],
        });
      } catch (error) {
        console.error("[zep-persist] message.received failed", error);
      }
    },

    async "message.completed"(event, ctx) {
      try {
        // Interim narration before tools also emits message.completed with
        // finishReason "tool-calls". Persist every other completion (stop,
        // length, content-filter, etc.) as the turn's assistant message.
        if (event.data.finishReason === "tool-calls") return;

        const text = event.data.message?.trim();
        if (!text) return;

        const identity = resolveZepIdentity(ctx);
        await ensureZepUserAndThread(identity);

        await getZepClient().thread.addMessages(identity.threadId, {
          messages: [
            {
              role: "assistant",
              name: "Eve Agent",
              content: text,
            },
          ],
        });
      } catch (error) {
        console.error("[zep-persist] message.completed failed", error);
      }
    },
  },
});

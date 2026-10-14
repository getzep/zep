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
 * (`turn.started` dynamic instructions, with the utterance stashed from the
 * channel's `onMessage`). This hook only writes to Zep.
 *
 * Hooks are observe-only and at-least-once. Failures are swallowed so a Zep
 * outage never fails the Eve turn.
 */
export default defineHook({
  events: {
    async "session.started"(_event, ctx) {
      try {
        const identity = resolveZepIdentity(ctx);
        bindPendingUtteranceToSession({
          sessionId: ctx.session.id,
          userId: identity.userId,
        });
        const zepIdentity = await ensureZepUserAndThread(identity);
        // Warm is best-effort and must not delay first-turn recall.
        void getZepClient()
          .graph.warm(zepIdentity.userUuid)
          .catch((error) => {
            console.warn("[zep-persist] graph.warm failed", error);
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
        const zepIdentity = await ensureZepUserAndThread(identity);

        await getZepClient().thread.addMessages(zepIdentity.threadUuid, {
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
        const zepIdentity = await ensureZepUserAndThread(identity);

        await getZepClient().thread.addMessages(zepIdentity.threadUuid, {
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

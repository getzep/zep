/**
 * Core persist-and-inject logic shared by `createZepBeforeModelCallback` and
 * `ZepContextTool`.
 *
 * On each turn it:
 *   1. Extracts the latest user message text.
 *   2. Resolves the Zep identity (explicit options → session state → ADK IDs).
 *   3. Ensures the Zep user and thread exist.
 *   4. Persists the user message and retrieves a Context Block in a single
 *      `thread.addMessages(returnContext: true)` round-trip.
 *   5. Injects the Context Block into `llmRequest.config.systemInstruction`.
 *
 * Every Zep call is wrapped so a failure is logged and the turn proceeds
 * without injected context — a Zep outage never crashes the agent.
 */

import type { ZepClient, Zep } from "@getzep/zep-cloud";
import type { LlmRequest } from "@google/adk";
import type { GenerateContentConfig, Part } from "@google/genai";
import {
  extractText,
  resolveIdentity,
  type AdkContextLike,
  type ResolvedIdentity,
  type ZepIdentityOptions,
} from "./identity.js";
import type { Logger } from "./logging.js";
import type { ZepResourceManager } from "./resources.js";

/** Header wrapped around the Zep Context Block when injected into the prompt. */
const CONTEXT_HEADER =
  "The following context is retrieved from Zep, the agent's long-term memory. " +
  "It contains relevant facts, entities, and prior knowledge about the user. " +
  "Use it to inform your response.";

/**
 * Build the system-instruction snippet that carries the Zep Context Block.
 *
 * Exposed for testing and for callers that assemble their own prompt.
 *
 * @param contextBlock The Context Block returned by Zep.
 * @returns A prompt-ready instruction wrapping the block in delimiters.
 */
export function formatContextInstruction(contextBlock: string): string {
  return `${CONTEXT_HEADER}\n\n<ZEP_CONTEXT>\n${contextBlock}\n</ZEP_CONTEXT>`;
}

/**
 * Append a system-instruction string to an `LlmRequest`, preserving any
 * existing instruction.
 *
 * ADK ships `appendInstructions`, but its module is not reachable under
 * NodeNext (the package only exports `"."`), so we mutate
 * `config.systemInstruction` directly. `systemInstruction` accepts a string
 * (`ContentUnion`), so we concatenate when one is already present.
 */
function appendSystemInstruction(
  llmRequest: LlmRequest,
  instruction: string,
): void {
  const config: GenerateContentConfig = (llmRequest.config ??= {});
  const existing = config.systemInstruction;

  if (existing === undefined || existing === null) {
    config.systemInstruction = instruction;
    return;
  }

  if (typeof existing === "string") {
    config.systemInstruction = `${existing}\n\n${instruction}`;
    return;
  }

  // `systemInstruction` may also be a `Content`, a `Part[]`, or a single
  // `Part`. Normalise to a `Part[]` (a valid `ContentUnion`) and append our
  // instruction as a trailing text part, preserving the existing instruction.
  const parts: Part[] = [];
  for (const item of Array.isArray(existing) ? existing : [existing]) {
    if (typeof item === "string") {
      parts.push({ text: item });
    } else if ("parts" in item && Array.isArray(item.parts)) {
      // A `Content` object — pull out its parts.
      parts.push(...item.parts);
    } else {
      // A single `Part`.
      parts.push(item as Part);
    }
  }
  parts.push({ text: instruction });
  config.systemInstruction = parts;
}

/** Options accepted by {@link persistAndInject}. */
export interface InjectOptions extends ZepIdentityOptions {
  /**
   * Roles to exclude from Zep's knowledge-graph ingestion. The messages are
   * still stored in the thread and used to contextualize other messages.
   */
  ignoreRoles?: Zep.RoleType[];
}

/**
 * Persist the latest user message to Zep and inject the returned Context Block
 * into the outgoing LLM request.
 *
 * This is the single implementation behind both the `beforeModelCallback` and
 * `ZepContextTool`. It never throws on a Zep error.
 *
 * @returns The injected Context Block, or `undefined` if nothing was injected
 *   (no user text, identity unresolved, or a Zep failure).
 */
export async function persistAndInject(params: {
  zep: ZepClient;
  resources: ZepResourceManager;
  logger: Logger;
  context: AdkContextLike;
  llmRequest: LlmRequest;
  options: InjectOptions;
}): Promise<string | undefined> {
  const { zep, resources, logger, context, llmRequest, options } = params;

  const userText = extractText(context.userContent);
  if (!userText) {
    return undefined;
  }

  let identity: ResolvedIdentity;
  try {
    identity = resolveIdentity(context, options);
  } catch (error) {
    logger.warn(
      "Skipping Zep persistence for this turn — could not resolve identity",
      error,
    );
    return undefined;
  }

  if (!(await resources.ensure(identity))) {
    return undefined;
  }

  let contextBlock: string | undefined;
  try {
    const response = await zep.thread.addMessages(identity.threadId, {
      messages: [
        {
          role: "user",
          content: userText,
          name: identity.displayName,
        },
      ],
      returnContext: true,
      ignoreRoles: options.ignoreRoles,
    });
    contextBlock = response.context;
    logger.info(
      `Persisted user message to Zep (thread=${identity.threadId}); ` +
        `context length: ${contextBlock?.length ?? 0}`,
    );
  } catch (error) {
    logger.warn("Failed to persist message / retrieve context from Zep", error);
    return undefined;
  }

  if (!contextBlock) {
    return undefined;
  }

  appendSystemInstruction(llmRequest, formatContextInstruction(contextBlock));
  return contextBlock;
}

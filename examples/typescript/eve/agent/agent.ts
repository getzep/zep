import { createGoogleGenerativeAI } from "@ai-sdk/google";
import { defineAgent } from "eve";

/**
 * Calls Google Gemini directly (not via AI Gateway BYOK).
 * Gateway BYOK for Google expects Vertex service-account creds; a plain
 * GOOGLE_API_KEY works with @ai-sdk/google instead.
 *
 * If you prefer Vercel AI Gateway credits, set AI_GATEWAY_API_KEY and use a
 * string model id like "google/gemini-2.5-flash" (no provider package needed).
 */
const google = createGoogleGenerativeAI({
  apiKey: process.env.GOOGLE_API_KEY,
});

export default defineAgent({
  model: google("gemini-2.5-flash"),
});

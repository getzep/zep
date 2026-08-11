import type { UserContent } from "ai";

/** Flatten Eve / AI SDK user content into plain text for Zep queries. */
export function flattenUserContent(message: string | UserContent): string {
  if (typeof message === "string") return message.trim();
  if (!Array.isArray(message)) return "";

  const parts: string[] = [];
  for (const part of message) {
    if (
      typeof part === "object" &&
      part !== null &&
      "type" in part &&
      part.type === "text" &&
      "text" in part &&
      typeof part.text === "string"
    ) {
      parts.push(part.text);
    }
  }
  return parts.join("\n").trim();
}

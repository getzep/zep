import type { BankingTools } from "./tools.js";

export type EpisodeResult = {
  finalAnswer: string;
  tokens: number;
  tools: BankingTools;
};

export function textContent(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    return value.map((item) => {
      if (typeof item === "string") return item;
      if (item && typeof item === "object" && "text" in item) return String(item.text);
      return "";
    }).join("");
  }
  return "";
}

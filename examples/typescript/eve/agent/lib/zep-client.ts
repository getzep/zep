import { ZepClient } from "@getzep/zep-cloud";

let client: ZepClient | null = null;

export function getZepClient(): ZepClient {
  if (client) return client;

  const apiKey = process.env.ZEP_API_KEY?.trim();
  if (!apiKey) {
    throw new Error(
      "ZEP_API_KEY is missing. Copy .env.example to .env and set your Zep Cloud API key.",
    );
  }

  const baseUrl = process.env.ZEP_API_URL?.trim();
  client = new ZepClient({
    apiKey,
    ...(baseUrl ? { baseUrl } : {}),
  });
  return client;
}

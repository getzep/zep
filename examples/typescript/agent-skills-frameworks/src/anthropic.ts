export function getAnthropicBaseURL(): string | undefined {
  const baseURL = process.env.ANTHROPIC_BASE_URL;
  if (!baseURL) return undefined;
  const normalized = baseURL.replace(/\/+$/, "");
  return normalized.endsWith("/v1") ? normalized : `${normalized}/v1`;
}

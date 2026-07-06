/**
 * Error types for the Zep Google ADK integration.
 */

/**
 * Raised when Zep identity (user ID / thread ID) cannot be resolved from the
 * ADK callback or tool context.
 *
 * This is the only error the integration throws by design. All Zep API
 * failures are caught internally, logged, and swallowed so that a Zep outage
 * can never crash the host agent.
 */
export class ZepIdentityError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ZepIdentityError";
    // Restore the prototype chain for instanceof checks under ES5/CJS targets.
    Object.setPrototypeOf(this, ZepIdentityError.prototype);
  }
}

/**
 * Detect whether `error` represents a Zep "not found" failure (404) — the
 * shape returned when a persist call targets a user or thread that was
 * never provisioned with `ensureUser()` / `ensureThread()`.
 *
 * Internal to the package; not exported from `index.ts`.
 */
export function isNotFoundError(error: unknown): boolean {
  const statusCode = (error as { statusCode?: unknown } | null)?.statusCode;
  if (statusCode === 404) {
    return true;
  }
  const text = String(
    error instanceof Error ? error.message : error,
  ).toLowerCase();
  return text.includes("not found") || text.includes("404");
}

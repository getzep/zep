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

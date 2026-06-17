/**
 * Minimal logging abstraction.
 *
 * The integration logs Zep failures rather than throwing them, so a Zep
 * outage never crashes the host agent. Callers can supply their own logger;
 * the default writes to `console`.
 */

/** Structured logger used throughout the integration. */
export interface Logger {
  debug(message: string, ...args: unknown[]): void;
  info(message: string, ...args: unknown[]): void;
  warn(message: string, ...args: unknown[]): void;
  error(message: string, ...args: unknown[]): void;
}

const PREFIX = "[zep-adk]";

/** Default logger backed by `console`, namespaced with a `[zep-adk]` prefix. */
export const defaultLogger: Logger = {
  debug: (message, ...args) => console.debug(`${PREFIX} ${message}`, ...args),
  info: (message, ...args) => console.info(`${PREFIX} ${message}`, ...args),
  warn: (message, ...args) => console.warn(`${PREFIX} ${message}`, ...args),
  error: (message, ...args) => console.error(`${PREFIX} ${message}`, ...args),
};

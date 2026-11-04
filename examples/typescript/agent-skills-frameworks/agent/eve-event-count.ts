import { readFileSync, writeFileSync } from "node:fs";

type EventCounter = { eventCount: number };

export function writeHookEventCount(path: string, eventCount: number) {
  writeFileSync(path, `${eventCount}\n`);
}

export function addHookEventCount(recorder: EventCounter, path: string) {
  const eventCount = Number(readFileSync(path, "utf8").trim());
  if (!Number.isSafeInteger(eventCount) || eventCount < 0) {
    throw new Error("The Eve hook event count is not a non-negative integer.");
  }
  recorder.eventCount += eventCount;
}

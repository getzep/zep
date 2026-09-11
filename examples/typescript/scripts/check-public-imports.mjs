#!/usr/bin/env node
/**
 * Static guard: loose examples must use public @getzep/zep-cloud exports only.
 * Fails if any snippet still imports the old in-repo ../../src SDK paths or
 * other non-public package subpaths.
 */
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const groups = ["graph", "memory", "users"];

const forbidden = [
  {
    name: "legacy in-repo SDK import",
    pattern: /from\s+["']\.\.\/\.\.\/src(?:\/[^"']*)?["']/,
  },
  {
    name: "non-public @getzep/zep-cloud subpath",
    pattern: /from\s+["']@getzep\/zep-cloud\/(?!serialization(?:["']|$))[^"']+["']/,
  },
];

async function listTsFiles(dir) {
  const entries = await readdir(dir, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...(await listTsFiles(full)));
    } else if (entry.isFile() && entry.name.endsWith(".ts")) {
      files.push(full);
    }
  }
  return files;
}

const violations = [];

for (const group of groups) {
  const files = await listTsFiles(path.join(root, group));
  for (const file of files) {
    const source = await readFile(file, "utf8");
    for (const rule of forbidden) {
      if (rule.pattern.test(source)) {
        violations.push(`${path.relative(root, file)}: ${rule.name}`);
      }
    }
  }
}

const usersSource = await readFile(path.join(root, "users", "users.ts"), "utf8");
if (
  /listOrdered\s*\(/.test(usersSource) &&
  /user\.(update|delete)\s*\(\s*(?:await\s+)?client\.user\.listOrdered/.test(
    usersSource.replace(/\s+/g, " "),
  )
) {
  violations.push(
    "users/users.ts: update/delete must not use listOrdered results from the whole project",
  );
}
if (!/createdUserIds/.test(usersSource)) {
  violations.push(
    "users/users.ts: must track createdUserIds and only mutate those users",
  );
}

if (violations.length > 0) {
  console.error("Public-import check failed:");
  for (const v of violations) {
    console.error(`  - ${v}`);
  }
  process.exit(1);
}

console.log(
  `Public-import check passed (${groups.join(", ")} use only public @getzep/zep-cloud exports).`,
);

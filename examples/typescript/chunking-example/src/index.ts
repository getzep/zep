import * as fs from "fs";
import * as path from "path";
import { config } from "dotenv";
import { ZepClient } from "@getzep/zep-cloud";
import OpenAI from "openai";
import {
  CommanderError,
  parseCliArgs,
  type CliOptions,
} from "./cli";

// Load environment variables
config();

const ZEP_MAX_EPISODE_SIZE = 10000;
const OPENAI_MODEL = "gpt-4o-mini";
const MAX_RETRIES = 3;
const BASE_DELAY_MS = 1000;

/**
 * Sleep for a specified number of milliseconds
 */
function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Split text into sentences
 */
function splitIntoSentences(text: string): string[] {
  const sentences = text.match(/[^.!?]+[.!?]+[\s]*/g) || [text];
  return sentences.map((s) => s.trim()).filter((s) => s.length > 0);
}

/**
 * Chunk a document into smaller pieces with overlap
 */
export function chunkDocument(
  document: string,
  chunkSize: number,
  chunkOverlap: number,
): string[] {
  const chunks: string[] = [];
  const paragraphs = document.split(/\n\n+/).filter((p) => p.trim().length > 0);

  let currentChunk = "";

  for (const paragraph of paragraphs) {
    const trimmedParagraph = paragraph.trim();

    if (trimmedParagraph.length > chunkSize) {
      if (currentChunk.length > 0) {
        chunks.push(currentChunk.trim());
        currentChunk = currentChunk.slice(-chunkOverlap);
      }

      const sentences = splitIntoSentences(trimmedParagraph);

      for (const sentence of sentences) {
        if (currentChunk.length + sentence.length + 1 > chunkSize) {
          if (currentChunk.length > 0) {
            chunks.push(currentChunk.trim());
            currentChunk = currentChunk.slice(-chunkOverlap);
          }
        }

        if (currentChunk.length > 0) {
          currentChunk += " " + sentence;
        } else {
          currentChunk = sentence;
        }
      }
    } else {
      if (currentChunk.length + trimmedParagraph.length + 2 > chunkSize) {
        if (currentChunk.length > 0) {
          chunks.push(currentChunk.trim());
          currentChunk = currentChunk.slice(-chunkOverlap);
        }
      }

      if (currentChunk.length > 0) {
        currentChunk += "\n\n" + trimmedParagraph;
      } else {
        currentChunk = trimmedParagraph;
      }
    }
  }

  if (currentChunk.trim().length > 0) {
    chunks.push(currentChunk.trim());
  }

  return chunks;
}

/**
 * Add context to a chunk using OpenAI
 */
async function contextualizeChunk(
  openai: OpenAI,
  fullDocument: string,
  chunk: string,
): Promise<string> {
  const prompt = `<document>
${fullDocument}
</document>

Here is the chunk we want to situate within the whole document:
<chunk>
${chunk}
</chunk>

Please give a short succinct context to situate this chunk within the overall document for the purposes of improving search retrieval of the chunk. If the document has a publication date, please include the date in your context. Answer only with the succinct context and nothing else.`;

  let lastError: Error | null = null;

  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    try {
      const response = await openai.chat.completions.create({
        model: OPENAI_MODEL,
        messages: [{ role: "user", content: prompt }],
        max_completion_tokens: 256,
      });

      const context = response.choices[0]?.message?.content?.trim() || "";
      return `${context}\n\n---\n\n${chunk}`;
    } catch (error) {
      lastError = error as Error;

      if (
        error instanceof Error &&
        (error.message.includes("rate_limit") || error.message.includes("429"))
      ) {
        const delay = BASE_DELAY_MS * Math.pow(2, attempt);
        console.log(`Rate limited. Waiting ${delay}ms before retry...`);
        await sleep(delay);
      } else {
        throw error;
      }
    }
  }

  throw lastError || new Error("Failed to contextualize chunk after retries");
}

/**
 * Validate and truncate contextualized chunk if needed
 */
function validateAndTruncate(
  contextualizedChunk: string,
  originalChunk: string,
): string {
  if (contextualizedChunk.length <= ZEP_MAX_EPISODE_SIZE) {
    return contextualizedChunk;
  }

  console.log(
    `Warning: Chunk exceeds ${ZEP_MAX_EPISODE_SIZE} chars. Truncating context...`,
  );

  const separatorIndex = contextualizedChunk.indexOf("\n\n---\n\n");

  if (separatorIndex === -1) {
    return contextualizedChunk.slice(0, ZEP_MAX_EPISODE_SIZE);
  }

  const separatorAndChunk = "\n\n---\n\n" + originalChunk;
  const availableForContext = ZEP_MAX_EPISODE_SIZE - separatorAndChunk.length;

  if (availableForContext <= 0) {
    return originalChunk.slice(0, ZEP_MAX_EPISODE_SIZE);
  }

  const context = contextualizedChunk.slice(0, separatorIndex);
  const truncatedContext = context.slice(0, availableForContext);

  return `${truncatedContext}\n\n---\n\n${originalChunk}`;
}

/**
 * Ensure a user exists in Zep
 */
async function ensureUserExists(
  client: ZepClient,
  userId: string,
): Promise<void> {
  try {
    await client.user.get(userId);
    console.log(`User ${userId} already exists.`);
  } catch (error) {
    console.log(`Creating user ${userId}...`);
    await client.user.add({ userId });
    console.log(`User ${userId} created.`);
  }
}

/**
 * Wait until an episode has been processed
 */
async function waitForEpisode(
  client: ZepClient,
  episodeUuid: string,
): Promise<void> {
  const timeoutMs = 180_000;
  const pollMs = 2000;
  const started = Date.now();

  while (Date.now() - started < timeoutMs) {
    const episode = await client.graph.episode.get(episodeUuid);
    if (episode.processed) {
      console.log(`  Episode ${episodeUuid} processed`);
      return;
    }
    await sleep(pollMs);
  }

  throw new Error(
    `Timed out waiting for episode ${episodeUuid} to process after ${timeoutMs}ms`,
  );
}

/**
 * Ingest a chunk to Zep with retry logic
 */
async function ingestToZep(
  client: ZepClient,
  userId: string,
  data: string,
): Promise<string | null> {
  let lastError: Error | null = null;

  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    try {
      const episode = await client.graph.add({
        userId,
        type: "text",
        data,
      });
      return episode.uuid;
    } catch (error) {
      lastError = error as Error;
      const delay = BASE_DELAY_MS * Math.pow(2, attempt);
      console.log(`Zep ingestion failed. Waiting ${delay}ms before retry...`);
      await sleep(delay);
    }
  }

  console.error(
    `Failed to ingest to Zep after ${MAX_RETRIES} attempts:`,
    lastError?.message,
  );
  return null;
}

/**
 * Process a document through the full pipeline
 */
export async function processDocument(options: CliOptions): Promise<void> {
  const { document: documentPath, userId, chunkSize, chunkOverlap, dryRun, wait } =
    options;

  const openaiApiKey = process.env.OPENAI_API_KEY;
  if (!openaiApiKey) {
    throw new Error("OPENAI_API_KEY environment variable is required");
  }

  const zepApiKey = process.env.ZEP_API_KEY;
  if (!dryRun && !zepApiKey) {
    throw new Error("ZEP_API_KEY environment variable is required unless --dry-run");
  }

  const absolutePath = path.resolve(documentPath);
  if (!fs.existsSync(absolutePath)) {
    throw new Error(`Document not found: ${absolutePath}`);
  }

  const documentContent = fs.readFileSync(absolutePath, "utf-8");
  console.log(`Loaded document: ${absolutePath} (${documentContent.length} chars)`);

  const openaiClient = new OpenAI({ apiKey: openaiApiKey });
  const zepClient = zepApiKey
    ? new ZepClient({ apiKey: zepApiKey })
    : null;

  console.log(`\nConfiguration:`);
  console.log(`  User ID: ${userId}`);
  console.log(`  Chunk size: ${chunkSize}`);
  console.log(`  Chunk overlap: ${chunkOverlap}`);
  console.log(`  Dry run: ${dryRun}`);
  console.log(`  Wait: ${wait}`);

  if (!dryRun && zepClient) {
    await ensureUserExists(zepClient, userId);
  }

  console.log(`\nChunking document...`);
  const chunks = chunkDocument(documentContent, chunkSize, chunkOverlap);
  console.log(`Created ${chunks.length} chunks`);

  let successCount = 0;
  let failCount = 0;

  for (let i = 0; i < chunks.length; i++) {
    const chunk = chunks[i];
    console.log(
      `\nProcessing chunk ${i + 1}/${chunks.length} (${chunk.length} chars)...`,
    );

    console.log(`  Contextualizing...`);
    let contextualizedChunk: string;
    try {
      contextualizedChunk = await contextualizeChunk(
        openaiClient,
        documentContent,
        chunk,
      );
    } catch (error) {
      console.error(`  Failed to contextualize: ${(error as Error).message}`);
      failCount++;
      continue;
    }

    contextualizedChunk = validateAndTruncate(contextualizedChunk, chunk);
    console.log(
      `  Contextualized chunk size: ${contextualizedChunk.length} chars`,
    );

    if (dryRun) {
      console.log(`  Dry run — skipping Zep ingestion`);
      successCount++;
      continue;
    }

    if (!zepClient) {
      throw new Error("Zep client not initialized");
    }

    console.log(`  Ingesting to Zep...`);
    const episodeUuid = await ingestToZep(zepClient, userId, contextualizedChunk);

    if (episodeUuid) {
      console.log(`  Successfully ingested chunk ${i + 1} (${episodeUuid})`);
      successCount++;
      if (wait) {
        await waitForEpisode(zepClient, episodeUuid);
      }
    } else {
      console.log(`  Failed to ingest chunk ${i + 1}`);
      failCount++;
    }
  }

  console.log("\n" + "=".repeat(50));
  console.log("Processing complete!");
  console.log(`Successfully processed: ${successCount}`);
  console.log(`Failed: ${failCount}`);
  console.log("=".repeat(50));
}

async function main(): Promise<void> {
  try {
    const options = parseCliArgs(process.argv);
    await processDocument(options);
  } catch (error) {
    if (
      error instanceof CommanderError &&
      (error.code === "commander.helpDisplayed" ||
        error.code === "commander.version")
    ) {
      process.exit(error.exitCode);
    }
    throw error;
  }
}

if (require.main === module) {
  main().catch((error) => {
    console.error("Fatal error:", error);
    process.exit(1);
  });
}

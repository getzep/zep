import * as fs from "fs";
import * as path from "path";
import { createHash, randomUUID } from "node:crypto";
import { config } from "dotenv";
import { ZepClient } from "@getzep/zep-cloud";
import OpenAI from "openai";
import { Command } from "commander";

// Load environment variables
config();

interface CliOptions {
  document: string;
  userUuid?: string;
  chunkSize: number;
  chunkOverlap: number;
  dryRun: boolean;
  wait: boolean;
}

function parseCliArgs(argv: string[]): CliOptions {
  const program = new Command()
    .name("chunking-example")
    .description(
      "Chunk a document, contextualize each chunk with OpenAI, and ingest into Zep",
    )
    .argument("<document>", "Path to the document to process")
    .option("--user-uuid <uuid>", "Existing Zep user UUID for the knowledge graph")
    .option("--chunk-size <n>", "Maximum characters per chunk", "6000")
    .option("--chunk-overlap <n>", "Character overlap between chunks", "200")
    .option("--dry-run", "Process without ingesting to Zep", false)
    .option("--wait", "Wait once after all chunks are submitted", false)
    .allowExcessArguments(false);

  program.parse(argv);
  const opts = program.opts();

  return {
    document: program.args[0],
    userUuid: opts.userUuid ? String(opts.userUuid) : undefined,
    chunkSize: Number(opts.chunkSize),
    chunkOverlap: Number(opts.chunkOverlap),
    dryRun: Boolean(opts.dryRun),
    wait: Boolean(opts.wait),
  };
}

const ZEP_MAX_EPISODE_SIZE = 10000;
const OPENAI_MODEL = "gpt-5-mini";
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
  const sentences: string[] = [];
  let start = 0;

  for (let index = 0; index < text.length; index++) {
    if (text[index] !== "." && text[index] !== "!" && text[index] !== "?") {
      continue;
    }

    while (
      index + 1 < text.length &&
      (text[index + 1] === "." ||
        text[index + 1] === "!" ||
        text[index + 1] === "?")
    ) {
      index++;
    }

    const sentence = text.slice(start, index + 1).trim();
    if (sentence) {
      sentences.push(sentence);
    }

    start = index + 1;
    while (start < text.length && text[start].trim() === "") {
      start++;
    }
    index = start - 1;
  }

  const remainder = text.slice(start).trim();
  if (remainder) {
    sentences.push(remainder);
  }

  return sentences;
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
 * Wait until an ingestion task has completed
 */
async function waitForTask(
  client: ZepClient,
  taskUuid: string,
): Promise<void> {
  const timeoutMs = 1_200_000;
  const pollMs = 2000;
  const started = Date.now();
  let nextStatusReport = started + 30_000;

  while (Date.now() - started < timeoutMs) {
    const task = await client.task.get(taskUuid);
    if (task.status === "succeeded") return;
    if (task.status === "partial") {
      console.warn(`Warning: task ${taskUuid} ended with partial status.`);
      return;
    }
    if (task.status === "failed") {
      throw new Error(`Ingestion task failed: ${task.error?.message ?? ""}`);
    }
    if (Date.now() >= nextStatusReport) {
      console.log(`Task ${taskUuid} status=${task.status}; still waiting.`);
      nextStatusReport = Date.now() + 30_000;
    }
    await sleep(pollMs);
  }

  throw new Error(`Timed out waiting for ingestion task ${taskUuid} after ${timeoutMs}ms`);
}

/**
 * Ingest a chunk to Zep with retry logic
 */
async function ingestToZep(
  client: ZepClient,
  userUuid: string,
  data: string,
  documentId: string,
): Promise<{ episodeUuid?: string; taskUuid?: string } | null> {
  let lastError: Error | null = null;

  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    try {
      const episode = await client.graph.episode.add(userUuid, {
        type: "text",
        data,
        documentId,
        sourceDescription: "Contextualized document chunk",
      });
      return {
        episodeUuid: episode.episode?.uuid,
        taskUuid: episode.task?.uuid,
      };
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
  const { document: documentPath, userUuid: configuredUserUuid, chunkSize, chunkOverlap, dryRun, wait } =
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
  const documentId = createHash("sha256").update(documentContent).digest("hex");

  const openaiClient = new OpenAI({ apiKey: openaiApiKey });
  const zepClient = zepApiKey
    ? new ZepClient({ apiKey: zepApiKey })
    : null;

  console.log(`\nConfiguration:`);
  console.log(`  User UUID: ${configuredUserUuid ?? "created for this run"}`);
  console.log(`  Chunk size: ${chunkSize}`);
  console.log(`  Chunk overlap: ${chunkOverlap}`);
  console.log(`  Dry run: ${dryRun}`);
  console.log(`  Wait: ${wait}`);

  let userUuid = configuredUserUuid;
  if (!dryRun && zepClient && !userUuid) {
    const user = await zepClient.user.create({
      userId: `chunking-${randomUUID()}`,
      firstName: "Document",
      lastName: "Chunks",
      email: `chunking-${randomUUID()}@example.com`,
    });
    userUuid = user.uuid;
    if (!userUuid) throw new Error("Zep did not return a user UUID");
  }
  if (userUuid) console.log(`Zep user UUID: ${userUuid}`);

  console.log(`\nChunking document...`);
  const chunks = chunkDocument(documentContent, chunkSize, chunkOverlap);
  console.log(`Created ${chunks.length} chunks`);

  let successCount = 0;
  let failCount = 0;
  let lastTaskUuid: string | null = null;

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
    if (!userUuid) throw new Error("A Zep user UUID is required for ingestion");
    const result = await ingestToZep(zepClient, userUuid, contextualizedChunk, documentId);

    if (result) {
      console.log(
        `  Successfully submitted chunk ${i + 1} (episode ${result.episodeUuid ?? "created"})`,
      );
      if (result.taskUuid) lastTaskUuid = result.taskUuid;
      successCount++;
    } else {
      console.log(`  Failed to ingest chunk ${i + 1}`);
      failCount++;
    }
  }

  if (wait && zepClient && lastTaskUuid) {
    await waitForTask(zepClient, lastTaskUuid);
    console.log(`All submitted chunks are processed.`);
  }

  console.log("\n" + "=".repeat(50));
  console.log("Processing complete!");
  console.log(`Successfully processed: ${successCount}`);
  console.log(`Failed: ${failCount}`);
  console.log("=".repeat(50));
}

async function main(): Promise<void> {
  await processDocument(parseCliArgs(process.argv));
}

if (require.main === module) {
  main().catch((error) => {
    console.error("Fatal error:", error);
    process.exit(1);
  });
}

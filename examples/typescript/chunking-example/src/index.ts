import * as fs from "fs";
import * as path from "path";
import { config } from "dotenv";
import { ZepClient } from "@getzep/zep-cloud";
import OpenAI from "openai";

// Load environment variables
config();

// Configuration
const CHUNK_SIZE = 500;
const CHUNK_OVERLAP = 50;
const ZEP_MAX_EPISODE_SIZE = 10000;
const OPENAI_MODEL = "gpt-5-mini-2025-08-07";
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
function chunkDocument(document: string): string[] {
  const chunks: string[] = [];
  const paragraphs = document.split(/\n\n+/).filter((p) => p.trim().length > 0);

  let currentChunk = "";

  for (const paragraph of paragraphs) {
    const trimmedParagraph = paragraph.trim();

    if (trimmedParagraph.length > CHUNK_SIZE) {
      if (currentChunk.length > 0) {
        chunks.push(currentChunk.trim());
        currentChunk = currentChunk.slice(-CHUNK_OVERLAP);
      }

      const sentences = splitIntoSentences(trimmedParagraph);

      for (const sentence of sentences) {
        if (currentChunk.length + sentence.length + 1 > CHUNK_SIZE) {
          if (currentChunk.length > 0) {
            chunks.push(currentChunk.trim());
            currentChunk = currentChunk.slice(-CHUNK_OVERLAP);
          }
        }

        if (currentChunk.length > 0) {
          currentChunk += " " + sentence;
        } else {
          currentChunk = sentence;
        }
      }
    } else {
      if (currentChunk.length + trimmedParagraph.length + 2 > CHUNK_SIZE) {
        if (currentChunk.length > 0) {
          chunks.push(currentChunk.trim());
          currentChunk = currentChunk.slice(-CHUNK_OVERLAP);
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
  chunk: string
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
  originalChunk: string
): string {
  if (contextualizedChunk.length <= ZEP_MAX_EPISODE_SIZE) {
    return contextualizedChunk;
  }

  console.log(`Warning: Chunk exceeds ${ZEP_MAX_EPISODE_SIZE} chars. Truncating context...`);

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
  userId: string
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
 * Ingest a chunk to Zep with retry logic
 */
async function ingestToZep(
  client: ZepClient,
  userId: string,
  data: string
): Promise<boolean> {
  let lastError: Error | null = null;

  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    try {
      await client.graph.add({
        userId,
        type: "text",
        data,
      });
      return true;
    } catch (error) {
      lastError = error as Error;
      const delay = BASE_DELAY_MS * Math.pow(2, attempt);
      console.log(`Zep ingestion failed. Waiting ${delay}ms before retry...`);
      await sleep(delay);
    }
  }

  console.error(`Failed to ingest to Zep after ${MAX_RETRIES} attempts:`, lastError?.message);
  return false;
}

/**
 * Process a document through the full pipeline
 */
async function processDocument(documentPath: string, userId: string): Promise<void> {
  // Validate environment variables
  const zepApiKey = process.env.ZEP_API_KEY;
  const openaiApiKey = process.env.OPENAI_API_KEY;

  if (!zepApiKey) {
    throw new Error("ZEP_API_KEY environment variable is required");
  }

  if (!openaiApiKey) {
    throw new Error("OPENAI_API_KEY environment variable is required");
  }

  // Read document
  const absolutePath = path.resolve(documentPath);
  if (!fs.existsSync(absolutePath)) {
    throw new Error(`Document not found: ${absolutePath}`);
  }

  const documentContent = fs.readFileSync(absolutePath, "utf-8");
  console.log(`Loaded document: ${absolutePath} (${documentContent.length} chars)`);

  // Initialize clients
  const zepClient = new ZepClient({ apiKey: zepApiKey });
  const openaiClient = new OpenAI({ apiKey: openaiApiKey });

  console.log(`\nConfiguration:`);
  console.log(`  User ID: ${userId}`);
  console.log(`  Chunk size: ${CHUNK_SIZE}`);
  console.log(`  Chunk overlap: ${CHUNK_OVERLAP}`);

  // Ensure user exists
  await ensureUserExists(zepClient, userId);

  // Chunk the document
  console.log(`\nChunking document...`);
  const chunks = chunkDocument(documentContent);
  console.log(`Created ${chunks.length} chunks`);

  // Process each chunk
  for (let i = 0; i < chunks.length; i++) {
    const chunk = chunks[i];
    console.log(`\nProcessing chunk ${i + 1}/${chunks.length} (${chunk.length} chars)...`);

    // Contextualize the chunk
    console.log(`  Contextualizing...`);
    let contextualizedChunk: string;
    try {
      contextualizedChunk = await contextualizeChunk(openaiClient, documentContent, chunk);
    } catch (error) {
      console.error(`  Failed to contextualize: ${(error as Error).message}`);
      continue;
    }

    // Validate and truncate if needed
    contextualizedChunk = validateAndTruncate(contextualizedChunk, chunk);
    console.log(`  Contextualized chunk size: ${contextualizedChunk.length} chars`);

    // Ingest to Zep
    console.log(`  Ingesting to Zep...`);
    const success = await ingestToZep(zepClient, userId, contextualizedChunk);

    if (success) {
      console.log(`  Successfully ingested chunk ${i + 1}`);
    } else {
      console.log(`  Failed to ingest chunk ${i + 1}`);
    }
  }

  console.log("\n" + "=".repeat(50));
  console.log("Processing complete!");
  console.log("=".repeat(50));
}

// Example usage - modify these values as needed
const DOCUMENT_PATH = "sample_document.txt";
const USER_ID = "example-user";

processDocument(DOCUMENT_PATH, USER_ID).catch((error) => {
  console.error("Fatal error:", error);
  process.exit(1);
});

import { Command, CommanderError } from "commander";

export interface CliOptions {
  document: string;
  userId: string;
  chunkSize: number;
  chunkOverlap: number;
  dryRun: boolean;
  wait: boolean;
}

export { CommanderError };

/**
 * Parse chunking-example CLI arguments.
 */
export function parseCliArgs(argv: string[]): CliOptions {
  const program = new Command();
  program
    .name("chunking-example")
    .description(
      "Chunk a document, contextualize each chunk with OpenAI, and ingest into Zep",
    )
    .argument("<document>", "Path to the document to process")
    .requiredOption("--user-id <id>", "Zep user ID for the knowledge graph")
    .option("--chunk-size <n>", "Maximum characters per chunk", "6000")
    .option("--chunk-overlap <n>", "Character overlap between chunks", "200")
    .option("--dry-run", "Process without ingesting to Zep", false)
    .option("--wait", "Wait for processing after each chunk", false)
    .allowExcessArguments(false)
    .exitOverride();

  try {
    program.parse(argv);
  } catch (error) {
    if (error instanceof CommanderError) {
      if (
        error.code === "commander.helpDisplayed" ||
        error.code === "commander.version"
      ) {
        throw error;
      }
      if (
        error.code === "commander.missingArgument" ||
        error.code === "commander.missingMandatoryOptionValue"
      ) {
        throw new Error("document path and --user-id are required");
      }
      throw new Error(error.message);
    }
    throw error;
  }

  const opts = program.opts();
  const document = program.args[0];
  if (!document || !opts.userId) {
    throw new Error("document path and --user-id are required");
  }

  return {
    document,
    userId: String(opts.userId),
    chunkSize: Number(opts.chunkSize),
    chunkOverlap: Number(opts.chunkOverlap),
    dryRun: Boolean(opts.dryRun),
    wait: Boolean(opts.wait),
  };
}

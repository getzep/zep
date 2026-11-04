import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { makeCase, expectedDatabase, score, type Case } from "./banking.js";
import type { EpisodeResult } from "./episode.js";
import { ZepSkills, TraceRecorder, FAMILY } from "./zep-skills.js";

type Framework = "mastra" | "codex" | "eve";
type Adapter = (
  caseData: Case,
  model: string,
  effort: string,
  skills: Record<string, unknown>[],
  recorder: TraceRecorder,
  teaching: boolean,
) => Promise<EpisodeResult>;

export const FRAMEWORKS: Record<Framework, () => Promise<{ runEpisode: Adapter }>> = {
  mastra: () => import("../agent/mastra.js"),
  codex: () => import("../agent/codex.js"),
  eve: () => import("../agent/eve.js"),
};

type Options = {
  framework: Framework;
  output: string;
  teacher: string;
  teacherEffort: string;
  student: string;
  effort: string;
  teacherSeed: number;
  seed: number;
  compileTimeout: number;
};

function parseArgs(argv: string[]): Options | null {
  if (argv.includes("--help") || argv.includes("-h")) return null;
  const values: Record<string, string> = {};
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!key.startsWith("--") || !argv[index + 1] || argv[index + 1].startsWith("--")) {
      throw new Error(`Expected a value for ${key}.`);
    }
    values[key.slice(2)] = argv[index + 1];
    index += 1;
  }
  if (values.framework && !["mastra", "codex", "eve"].includes(values.framework)) {
    throw new Error("--framework must be mastra, codex, or eve.");
  }
  if (!values.output) throw new Error("Use --output <dir>.");
  const framework = (values.framework ?? "mastra") as Framework;
  return {
    framework,
    output: values.output,
    teacher: values.teacher ?? (framework === "codex" ? "gpt-6-astra" : "claude-opus-5-5"),
    teacherEffort: values["teacher-effort"] ?? (framework === "codex" ? "ultra" : "max"),
    student: values.student ?? (framework === "codex" ? "gpt-6-luna" : "claude-haiku-5-5"),
    effort: values.effort ?? (framework === "codex" ? "low" : "none"),
    teacherSeed: Number(values["teacher-seed"] ?? 22),
    seed: Number(values.seed ?? 10134),
    compileTimeout: Number(values["compile-timeout"] ?? 900),
  };
}

function help() {
  return `Usage: npm run run -- run --framework <mastra|codex|eve> --output <dir> [options]

Options:
  --teacher <model>          Teacher model (default: claude-opus-5-5; Codex: gpt-6-astra)
  --teacher-effort <effort>  Teacher reasoning effort (default: max; Codex: ultra)
  --student <model>          Student model (default: claude-haiku-5-5; Codex: gpt-6-luna)
  --effort <effort>          Student reasoning effort (default: none; Codex: low)
  --teacher-seed <integer>   Teacher case seed (default: 22)
  --seed <integer>           Student case seed (default: 10134)
  --compile-timeout <seconds>Skill compilation timeout (default: 900)
  --help                     Show this help
`;
}

function writeJson(path: string, value: unknown) {
  writeFileSync(path, `${JSON.stringify(value, null, 2)}\n`);
}

function checkLeakage(skills: Record<string, unknown>[], seeds: number[]) {
  const markdown = skills.map((item) => String(item.markdown ?? "")).join("\n");
  const hits: { case_id: string; value: string }[] = [];
  for (const seed of seeds) {
    const item = makeCase(seed);
    const database = expectedDatabase(item);
    const identifiers = [
      item.case_id,
      ...item.savings.map((account) => account.account_id),
      ...item.checking.map((account) => account.account_id),
      ...item.cards.map((card) => card.card_id),
    ];
    for (const value of identifiers) if (markdown.includes(value)) hits.push({ case_id: item.case_id, value });
    const amounts = [
      ...item.savings.map((account) => account.statement_balance),
      ...database.credits.map((credit) => credit.amount),
      ...database.reports.flatMap((report) => [report.expected_interest, report.net_interest_before_run]),
    ];
    for (const amount of new Set(amounts)) {
      const normalized = Number(amount).toFixed(2);
      const withoutTrailingZeroes = normalized.replace(/\.?0+$/, "");
      if (markdown.includes(normalized) || (withoutTrailingZeroes && markdown.includes(withoutTrailingZeroes))) {
        hits.push({ case_id: item.case_id, value: normalized });
      }
    }
  }
  return {
    exact_heldout_identifiers_balances_or_corrections: hits,
    source_id_suffixes: [...new Set(markdown.match(/(?<![\w-])[ck][1-3](?!\w)/gi) ?? [])].sort(),
    note: "This conservative exact-match check is not a semantic leakage review.",
  };
}

export async function runStage(
  path: string,
  zep: ZepSkills,
  agent: string,
  framework: Framework,
  caseData: Case,
  model: string,
  effort: string,
  skills: Record<string, unknown>[],
  teaching: boolean,
) {
  mkdirSync(path, { recursive: false });
  const recorder = new TraceRecorder(zep, agent);
  const start = Date.now();
  writeJson(`${path}/start.json`, { case: caseData, model, effort, teaching, skills });
  try {
    const traceUuid = await recorder.start();
    writeJson(`${path}/trace-start.json`, { trace_uuid: traceUuid, event_count: 0 });
    process.env.BANKING_AGENT_UUID = agent;
    process.env.BANKING_TRACE_UUID = traceUuid;
    process.env.BANKING_LEDGER_PATH = zep.ledger;
    const adapter = await FRAMEWORKS[framework]();
    const result = await adapter.runEpisode(caseData, model, effort, skills, recorder, teaching);
    // The score checks the banking state only. A student can pass without the Skill; the run then records the Skill use as ignored.
    const evaluation = score(caseData, result.tools.database);
    const seconds = (Date.now() - start) / 1000;
    const summary = { passed: evaluation.passed, tokens: result.tokens, seconds, final_answer: result.finalAnswer };
    writeJson(`${path}/tool-calls.json`, result.tools.calls);
    writeJson(`${path}/database.json`, result.tools.database);
    writeJson(`${path}/evaluation.json`, evaluation);
    writeJson(`${path}/result.json`, summary);
    const trace = await recorder.close(evaluation.passed);
    writeJson(`${path}/trace.json`, trace);
    return { ...summary, trace, tools: result.tools };
  } catch (error) {
    if (recorder.traceUuid && !recorder.closeRequested) {
      try {
        const trace = await recorder.close(false);
        writeJson(`${path}/trace.json`, trace);
      } catch (closeError) {
        writeJson(`${path}/trace-close-failure.json`, { error: String(closeError) });
      }
    }
    writeJson(`${path}/failure.json`, {
      type: error instanceof Error ? error.name : "UnknownError",
      error: String(error),
      trace_uuid: recorder.traceUuid,
      event_count: recorder.eventCount,
    });
    throw error;
  }
}

async function run(options: Options) {
  const output = resolve(options.output);
  mkdirSync(output, { recursive: true });
  writeJson(`${output}/config.json`, options);
  const zep = new ZepSkills(`${output}/zep-calls.jsonl`, options.framework);
  const agentData = await zep.createAgent();
  const agent = String(agentData.uuid);
  writeJson(`${output}/agent.json`, agentData);
  const teacher = await runStage(`${output}/teacher`, zep, agent, options.framework, makeCase(options.teacherSeed), options.teacher, options.teacherEffort, [], true);
  if (!teacher.passed) throw new Error("The teacher failed the local banking score.");
  const compiled = await zep.waitForSkill(agent, options.compileTimeout);
  writeJson(`${output}/learning-result.json`, compiled.runs);
  const skills = compiled.skills;
  mkdirSync(`${output}/skills`, { recursive: false });
  const catalog = skills.map((skill, index) => {
    const name = `skill-${String(index + 1).padStart(2, "0")}-${String(skill.uuid).replace(/[^A-Za-z0-9_-]/g, "_")}.md`;
    writeFileSync(`${output}/skills/${name}`, String(skill.markdown));
    return { uuid: skill.uuid, name: skill.name, description: skill.description, version: skill.version, markdown_file: `skills/${name}` };
  });
  writeJson(`${output}/skill-catalog.json`, catalog);
  const { searchId, skills: studentSkills } = await zep.searchSkills(agent);
  writeJson(`${output}/student-search.json`, { search_id: searchId, skills: studentSkills });
  const leakage = checkLeakage(studentSkills, [options.seed]);
  writeJson(`${output}/leakage-check.json`, leakage);
  if (leakage.exact_heldout_identifiers_balances_or_corrections.length || leakage.source_id_suffixes.length) {
    throw new Error("The Skill contains held-out case data.");
  }
  const student = await runStage(`${output}/student`, zep, agent, options.framework, makeCase(options.seed), options.student, options.effort, studentSkills, false);
  const uses = [];
  for (const skill of studentSkills) {
    const used = student.tools.calls.some((call) => call.name === "read_skill" && call.arguments.skill_uuid === skill.uuid);
    uses.push({
      skill_uuid: skill.uuid,
      ...await zep.recordSkillUse(agent, String(skill.uuid), searchId, student.trace.trace_uuid, used, student.passed),
    });
  }
  writeJson(`${output}/skill-uses.json`, uses);
  const summary = {
    framework: options.framework,
    agent_uuid: agent,
    family: FAMILY,
    teacher: { passed: teacher.passed, tokens: teacher.tokens, seconds: teacher.seconds, trace_uuid: teacher.trace.trace_uuid, event_count: teacher.trace.event_count },
    student: { passed: student.passed, tokens: student.tokens, seconds: student.seconds, trace_uuid: student.trace.trace_uuid, event_count: student.trace.event_count },
    learning_run_decision: compiled.runs.response.items?.[0]?.decision,
    skills: catalog,
    skill_uses: uses,
  };
  writeJson(`${output}/summary.json`, summary);
  console.log(JSON.stringify(summary, null, 2));
}

export async function main(argv = process.argv.slice(2)) {
  if (argv[0] === "--help" || argv[0] === "-h") {
    console.log(help());
    return;
  }
  if (argv[0] !== "run") throw new Error("Use the run command.");
  const options = parseArgs(argv.slice(1));
  if (!options) {
    console.log(help());
    return;
  }
  await run(options);
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}

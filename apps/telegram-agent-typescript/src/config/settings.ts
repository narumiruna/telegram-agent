import path from "node:path";

import { z } from "zod";

const booleanFromEnvironment = (defaultValue: boolean) =>
  z
    .preprocess((value) => {
      if (value === undefined || value === "") return defaultValue;
      if (typeof value === "boolean") return value;
      if (typeof value !== "string") return value;
      const normalized = value.trim().toLowerCase();
      if (["1", "true", "yes", "on"].includes(normalized)) return true;
      if (["0", "false", "no", "off"].includes(normalized)) return false;
      return value;
    }, z.boolean())
    .default(defaultValue);

const numberFromEnvironment = (
  defaultValue: number,
  constraints?: { integer?: boolean; min?: number; max?: number },
) => {
  let schema = constraints?.integer ? z.number().int() : z.number();
  if (constraints?.min !== undefined) schema = schema.min(constraints.min);
  if (constraints?.max !== undefined) schema = schema.max(constraints.max);
  return z.preprocess((value) => {
    if (value === undefined || value === "") return defaultValue;
    if (typeof value === "string") return Number(value);
    return value;
  }, schema);
};

const optionalString = z.preprocess((value) => {
  if (typeof value !== "string") return value;
  const normalized = value.trim();
  return normalized || undefined;
}, z.string().optional());

const csvStrings = z
  .string()
  .optional()
  .transform(
    (value) =>
      new Set(
        (value ?? "")
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
      ),
  );

const csvIntegers = z
  .string()
  .optional()
  .transform((value, context) => {
    const values = new Set<number>();
    for (const item of (value ?? "").split(",")) {
      const normalized = item.trim();
      if (!normalized) continue;
      const parsed = Number(normalized);
      if (!Number.isSafeInteger(parsed)) {
        context.addIssue({ code: "custom", message: `Expected an integer, received ${JSON.stringify(normalized)}` });
        return z.NEVER;
      }
      values.add(parsed);
    }
    return values;
  });

const environmentSchema = z.object({
  BOT_TOKEN: z.string().default(""),
  BOT_WHITELIST: csvIntegers,
  BOT_MAX_CONSECUTIVE_REPLIES_TO_BOTS: numberFromEnvironment(1, { integer: true, min: 0 }),
  BOT_GROUP_PASSIVE_CONTEXT_ENABLED: booleanFromEnvironment(true),
  BOT_SKILLS_DIR: z.string().default(".agents/skills"),
  BOT_ENABLED_SKILLS: csvStrings,
  BOT_SKILL_ADMINS: csvIntegers,
  BOT_SOUL_PATH: z.string().default("SOUL.md"),
  BOT_SOUL_REQUIRED: booleanFromEnvironment(false),
  BOT_SOUL_MAX_CHARS: numberFromEnvironment(8_000, { integer: true, min: 1 }),
  BOT_PROACTIVE_ENABLED: booleanFromEnvironment(true),
  BOT_PROACTIVE_URL_TIMEOUT_SECONDS: numberFromEnvironment(15, { min: Number.MIN_VALUE }),
  BOT_KABIGON_TIMEOUT_SECONDS: numberFromEnvironment(180, { min: Number.MIN_VALUE }),
  BOT_PROACTIVE_MAX_EXTRACTED_CHARS: numberFromEnvironment(12_000, { integer: true, min: 100 }),
  BOT_PROACTIVE_PENDING_TTL_SECONDS: numberFromEnvironment(900, { integer: true, min: 1 }),
  BOT_PROACTIVE_ALLOWED_SCHEMES: z
    .string()
    .default("http,https")
    .transform((value) => {
      return new Set(
        value
          .split(",")
          .map((item) => item.trim().toLowerCase())
          .filter(Boolean),
      );
    }),
  BOT_EVENTS_ENABLED: booleanFromEnvironment(false),
  BOT_EVENTS_DIR: z.string().default(".events"),
  BOT_EVENTS_SCAN_SECONDS: numberFromEnvironment(2, { min: Number.MIN_VALUE }),
  BOT_EVENTS_MAX_QUEUED_PER_CHAT: numberFromEnvironment(5, { integer: true, min: 1 }),
  BOT_EVENTS_MAX_TEXT_CHARS: numberFromEnvironment(4_000, { integer: true, min: 1 }),
  BOT_EVENTS_ARCHIVE_PROCESSED: booleanFromEnvironment(true),
  BOT_SESSION_LOG_DIR: z.string().default(".telegramagent/sessions"),
  BOT_AGENT_MAX_ATTEMPTS: numberFromEnvironment(3, { integer: true, min: 1 }),
  BOT_AGENT_RETRY_BASE_DELAY_SECONDS: numberFromEnvironment(1, { min: 0 }),
  BOT_AGENT_CONTEXT_TOKEN_BUDGET: numberFromEnvironment(100_000, { integer: true, min: 1 }),
  BOT_AGENT_COMPACTION_TRIGGER_RATIO: numberFromEnvironment(0.8, { min: Number.MIN_VALUE, max: 1 }),
  BOT_AGENT_CHARS_PER_TOKEN: numberFromEnvironment(4, { min: Number.MIN_VALUE }),
  BOT_TASKS_MAX_CONCURRENT_PER_CHAT: numberFromEnvironment(1, { integer: true, min: 1 }),
  BOT_DOCUMENT_INPUT_ENABLED: booleanFromEnvironment(true),
  BOT_DOCUMENT_MAX_BYTES: numberFromEnvironment(20_000_000, { integer: true, min: 1 }),
  BOT_DOCUMENT_MAX_MARKDOWN_CHARS: numberFromEnvironment(50_000, { integer: true, min: 1 }),
  BOT_DOCUMENT_CONVERSION_TIMEOUT_SECONDS: numberFromEnvironment(30, { min: Number.MIN_VALUE }),
  BOT_DOCUMENT_MAX_CONCURRENT_CONVERSIONS: numberFromEnvironment(2, { integer: true, min: 1 }),
  BOT_IMAGE_INPUT_ENABLED: booleanFromEnvironment(true),
  BOT_IMAGE_MAX_BYTES: numberFromEnvironment(8_000_000, { integer: true, min: 1 }),
  BOT_IMAGE_GENERATION_ENABLED: booleanFromEnvironment(false),
  BOT_IMAGE_GENERATION_MODEL: z.string().default("gpt-image-1"),
  BOT_IMAGE_GENERATION_SIZE: z.string().default("1024x1024"),
  BOT_IMAGE_GENERATION_TIMEOUT_SECONDS: numberFromEnvironment(120, { min: Number.MIN_VALUE }),
  BOT_CONTAINER_TOOLS_ENABLED: booleanFromEnvironment(false),
  BOT_CONTAINER_TOOLS_ROOT: z.string().default("."),
  BOT_CONTAINER_TOOLS_TIMEOUT_SECONDS: numberFromEnvironment(10, { min: Number.MIN_VALUE }),
  BOT_CONTAINER_TOOLS_MAX_OUTPUT_CHARS: numberFromEnvironment(12_000, { integer: true, min: 100 }),
  BOT_CONTAINER_TOOLS_MAX_READ_CHARS: numberFromEnvironment(20_000, { integer: true, min: 100 }),
  BOT_CONTAINER_TOOLS_MAX_RESULTS: numberFromEnvironment(200, { integer: true, min: 1 }),
  MORSEL_URL: z.url().default("https://morsel.narumi.dev/"),
  MORSEL_API_KEY: optionalString,
  MORSEL_MODE: z.enum(["disabled", "rich_only", "smart"]).default("smart"),
  MORSEL_LONG_REPLY_THRESHOLD: numberFromEnvironment(2_000, { integer: true, min: 1, max: 4_096 }),
  MORSEL_SHARE_EXPIRES_IN_SECONDS: numberFromEnvironment(2_592_000, {
    integer: true,
    min: 1,
    max: 315_360_000,
  }),
  MORSEL_TELEGRAM_INSTANT_VIEW: booleanFromEnvironment(true),
  TELEGRAM_INSTANT_VIEW_RHASH: optionalString,
  MORSEL_TIMEOUT_SECONDS: numberFromEnvironment(12, { min: Number.MIN_VALUE }),
  OPENAI_BASE_URL: z.url().default("https://api.openai.com/v1"),
  OPENAI_API_KEY: optionalString,
  OPENAI_MODEL: z.string().min(1).default("gpt-5.6-luna"),
});

export interface Settings {
  projectRoot: string;
  botToken: string;
  botWhitelist: ReadonlySet<number>;
  botMaxConsecutiveRepliesToBots: number;
  botGroupPassiveContextEnabled: boolean;
  botSkillsDir: string;
  botEnabledSkills: ReadonlySet<string>;
  botSkillAdmins: ReadonlySet<number>;
  botSoulPath: string;
  botSoulRequired: boolean;
  botSoulMaxChars: number;
  botProactiveEnabled: boolean;
  botProactiveUrlTimeoutSeconds: number;
  botProactiveMaxExtractedChars: number;
  botProactiveAllowedSchemes: ReadonlySet<string>;
  botSessionLogDir: string;
  botAgentMaxAttempts: number;
  botAgentRetryBaseDelaySeconds: number;
  botAgentContextTokenBudget: number;
  botAgentCompactionTriggerRatio: number;
  botImageInputEnabled: boolean;
  botImageMaxBytes: number;
  botContainerToolsEnabled: boolean;
  botContainerToolsRoot: string;
  morselUrl: string;
  morselApiKey?: string;
  morselMode: "disabled" | "rich_only" | "smart";
  morselLongReplyThreshold: number;
  morselShareExpiresInSeconds: number;
  morselTelegramInstantView: boolean;
  morselTelegramInstantViewRhash?: string;
  morselTimeoutSeconds: number;
  openaiBaseUrl: string;
  openaiApiKey?: string;
  openaiModel: string;
}

export function loadSettings(environment: NodeJS.ProcessEnv = process.env, projectRoot = process.cwd()): Settings {
  const parsed = environmentSchema.parse(environment);
  const root = path.resolve(projectRoot);
  return {
    projectRoot: root,
    botToken: parsed.BOT_TOKEN,
    botWhitelist: parsed.BOT_WHITELIST,
    botMaxConsecutiveRepliesToBots: parsed.BOT_MAX_CONSECUTIVE_REPLIES_TO_BOTS,
    botGroupPassiveContextEnabled: parsed.BOT_GROUP_PASSIVE_CONTEXT_ENABLED,
    botSkillsDir: path.resolve(root, parsed.BOT_SKILLS_DIR),
    botEnabledSkills: parsed.BOT_ENABLED_SKILLS,
    botSkillAdmins: parsed.BOT_SKILL_ADMINS,
    botSoulPath: path.resolve(root, parsed.BOT_SOUL_PATH),
    botSoulRequired: parsed.BOT_SOUL_REQUIRED,
    botSoulMaxChars: parsed.BOT_SOUL_MAX_CHARS,
    botProactiveEnabled: parsed.BOT_PROACTIVE_ENABLED,
    botProactiveUrlTimeoutSeconds: parsed.BOT_PROACTIVE_URL_TIMEOUT_SECONDS,
    botProactiveMaxExtractedChars: parsed.BOT_PROACTIVE_MAX_EXTRACTED_CHARS,
    botProactiveAllowedSchemes: parsed.BOT_PROACTIVE_ALLOWED_SCHEMES,
    botSessionLogDir: path.resolve(root, parsed.BOT_SESSION_LOG_DIR),
    botAgentMaxAttempts: parsed.BOT_AGENT_MAX_ATTEMPTS,
    botAgentRetryBaseDelaySeconds: parsed.BOT_AGENT_RETRY_BASE_DELAY_SECONDS,
    botAgentContextTokenBudget: parsed.BOT_AGENT_CONTEXT_TOKEN_BUDGET,
    botAgentCompactionTriggerRatio: parsed.BOT_AGENT_COMPACTION_TRIGGER_RATIO,
    botImageInputEnabled: parsed.BOT_IMAGE_INPUT_ENABLED,
    botImageMaxBytes: parsed.BOT_IMAGE_MAX_BYTES,
    botContainerToolsEnabled: parsed.BOT_CONTAINER_TOOLS_ENABLED,
    botContainerToolsRoot: path.resolve(root, parsed.BOT_CONTAINER_TOOLS_ROOT),
    morselUrl: parsed.MORSEL_URL,
    ...(parsed.MORSEL_API_KEY ? { morselApiKey: parsed.MORSEL_API_KEY } : {}),
    morselMode: parsed.MORSEL_MODE,
    morselLongReplyThreshold: parsed.MORSEL_LONG_REPLY_THRESHOLD,
    morselShareExpiresInSeconds: parsed.MORSEL_SHARE_EXPIRES_IN_SECONDS,
    morselTelegramInstantView: parsed.MORSEL_TELEGRAM_INSTANT_VIEW,
    ...(parsed.TELEGRAM_INSTANT_VIEW_RHASH
      ? { morselTelegramInstantViewRhash: parsed.TELEGRAM_INSTANT_VIEW_RHASH }
      : {}),
    morselTimeoutSeconds: parsed.MORSEL_TIMEOUT_SECONDS,
    openaiBaseUrl: parsed.OPENAI_BASE_URL.replace(/\/$/, ""),
    ...(parsed.OPENAI_API_KEY ? { openaiApiKey: parsed.OPENAI_API_KEY } : {}),
    openaiModel: parsed.OPENAI_MODEL,
  };
}

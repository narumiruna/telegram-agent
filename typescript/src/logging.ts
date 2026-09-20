import { inspect } from "node:util";

const telegramBotTokenPattern = /\/bot\d+:[A-Za-z0-9_-]+/g;
const firecrawlMcpPattern = /https:\/\/mcp\.firecrawl\.dev\/[^/\s]+\/v2\/mcp/gi;
const sensitiveValuePattern = /\b(token|api[_-]?key|authorization|cookie|set-cookie|password|secret)=([^\s;]+)/gi;
const bearerPattern = /\bBearer\s+[A-Za-z0-9._~+/=-]+/gi;

export function redactLogMessage(message: string): string {
  return message
    .replace(firecrawlMcpPattern, "https://mcp.firecrawl.dev/[redacted]/v2/mcp")
    .replace(telegramBotTokenPattern, "/bot[redacted]")
    .replace(sensitiveValuePattern, "$1=[redacted]")
    .replace(bearerPattern, "Bearer [redacted]");
}

export interface Logger {
  debug(message: string, details?: unknown): void;
  info(message: string, details?: unknown): void;
  warn(message: string, details?: unknown): void;
  error(message: string, details?: unknown): void;
}

export function createLogger(verbose = false): Logger {
  const write = (level: string, message: string, details?: unknown) => {
    const suffix = details === undefined ? "" : ` ${inspect(details, { depth: 5, breakLength: 120 })}`;
    process.stderr.write(`${new Date().toISOString()} | ${level} | ${redactLogMessage(message + suffix)}\n`);
  };

  return {
    debug: (message, details) => {
      if (verbose) write("DEBUG", message, details);
    },
    info: (message, details) => write("INFO", message, details),
    warn: (message, details) => write("WARN", message, details),
    error: (message, details) => write("ERROR", message, details),
  };
}

import type { AttemptRecord } from "./results.js"

export class KabigonError extends Error {
  constructor(message: string) {
    super(message)
    this.name = new.target.name
  }
}

export class LoaderError extends KabigonError {
  constructor(
    public readonly url: string,
    public readonly details: readonly string[] = [],
    public readonly attempts: readonly AttemptRecord[] = [],
  ) {
    const attempted =
      details.length > 0 ? `\n\nAttempted loaders:\n  - ${details.join("\n  - ")}` : ""
    super(`Failed to load URL: ${url}${attempted}`)
  }
}

export class InvalidUrlError extends KabigonError {
  constructor(
    public readonly url: string,
    public readonly expected: string,
  ) {
    super(`URL is not a ${expected} URL: ${url}`)
  }
}

export class ConfigurationError extends KabigonError {}

export class MissingRequirementError extends ConfigurationError {
  constructor(public readonly requirements: readonly string[]) {
    super(`Missing required environment variable(s): ${requirements.join(", ")}`)
  }
}

export class FirecrawlApiKeyNotSetError extends ConfigurationError {
  constructor() {
    super("FIRECRAWL_API_KEY is not set.")
  }
}

export class MissingDependencyError extends KabigonError {
  constructor(
    public readonly loaderName: string,
    public readonly dependency: string,
    public readonly hint: string,
  ) {
    super(`Loader ${JSON.stringify(loaderName)} requires ${JSON.stringify(dependency)}. ${hint}`)
  }
}

export class LoaderNotApplicableError extends KabigonError {
  constructor(
    public readonly loaderName: string,
    public readonly url: string,
    public readonly reason?: string,
  ) {
    super(`${loaderName} cannot handle URL: ${url}${reason ? ` - ${reason}` : ""}`)
  }
}

export class LoaderTimeoutError extends KabigonError {
  constructor(
    public readonly loaderName: string,
    public readonly url: string,
    public readonly timeoutSeconds: number,
    public readonly suggestion = "Try increasing the timeout or check your network connection.",
  ) {
    super(
      `${loaderName} timed out after ${timeoutSeconds}s while loading: ${url}\nSuggestion: ${suggestion}`,
    )
  }
}

export class LoaderContentError extends KabigonError {
  constructor(
    public readonly loaderName: string,
    public readonly url: string,
    public readonly reason: string,
    public readonly suggestion?: string,
  ) {
    super(
      `${loaderName} failed to extract content from: ${url} - ${reason}${suggestion ? `\nSuggestion: ${suggestion}` : ""}`,
    )
  }
}

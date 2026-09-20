#!/usr/bin/env node

import { realpathSync } from "node:fs"
import { fileURLToPath } from "node:url"

import { loadUrl } from "./api.js"
import { resolveExplicitLoadChain } from "./load-chain.js"
import { getLoaderDef, listLoaderDefs } from "./loader-registry.js"

interface CliOptions {
  list: boolean
  loaderNames?: string[]
  url?: string
}

function usage(): string {
  return "Usage: kabigon [--list] [--loader name[,name...]] URL"
}

export function parseArgs(args: readonly string[]): CliOptions {
  const options: CliOptions = { list: false }
  for (let index = 0; index < args.length; index += 1) {
    const value = args[index]
    if (value === "--list") options.list = true
    else if (value === "--verbose") continue
    else if (value === "--loader") {
      const raw = args[++index]
      if (!raw) throw new Error("--loader requires a comma-separated loader list")
      const names = raw
        .split(",")
        .map((name) => name.trim())
        .filter(Boolean)
      if (names.length === 0) throw new Error("Loader list cannot be empty.")
      for (const name of names) getLoaderDef(name)
      options.loaderNames = names
    } else if (value?.startsWith("-")) throw new Error(`Unknown option: ${value}`)
    else if (value) {
      if (options.url) throw new Error("Only one URL may be supplied")
      options.url = value
    }
  }
  return options
}

export async function main(args: readonly string[] = process.argv.slice(2)): Promise<void> {
  const options = parseArgs(args)
  if (options.list) {
    if (options.url || options.loaderNames)
      throw new Error("--list cannot be combined with URL or --loader.")
    for (const definition of listLoaderDefs({ cliVisible: true })) {
      console.log(`${definition.name} - ${definition.description}`)
    }
    return
  }
  if (!options.url) throw new Error(`URL is required unless --list is used.\n${usage()}`)
  const content = options.loaderNames
    ? await resolveExplicitLoadChain(options.url, options.loaderNames).load()
    : await loadUrl(options.url)
  console.log(content)
}

function isEntrypoint(): boolean {
  if (!process.argv[1]) return false
  try {
    return realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)
  } catch {
    return false
  }
}

if (isEntrypoint()) {
  main().catch((error: unknown) => {
    console.error(error instanceof Error ? error.message : String(error))
    process.exitCode = 1
  })
}

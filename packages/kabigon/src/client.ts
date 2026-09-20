import type { Browser } from "playwright";

import { remainingMilliseconds, withDeadline } from "./core/execution.js";
import { assertPublicUrl, type FetchImplementation, type PublicUrlResolver, safeFetch } from "./core/network.js";
import type { ImpersSession, ResourceProvider } from "./core/resources.js";
import type { LoadResult } from "./core/results.js";
import { resolveLoadChain } from "./load-chain.js";
import { createLoader, getLoaderDef } from "./loader-registry.js";
import { isPdfTarget } from "./sources/applicability.js";

const CLIENT_CONTEXT_REQUIRED = "Call KabigonClient.start() before loading URLs";
const INVALID_TARGET = "Target must be an HTTP(S) URL or a local PDF path";

class Semaphore {
  private available: number;
  private readonly waiters: Array<{
    resolve: () => void;
    reject: (reason: unknown) => void;
    signal?: AbortSignal;
    onAbort?: () => void;
  }> = [];

  constructor(limit: number) {
    this.available = limit;
  }

  async acquire(signal?: AbortSignal): Promise<void> {
    if (signal?.aborted) throw signal.reason;
    if (this.available > 0) {
      this.available -= 1;
      return;
    }
    await new Promise<void>((resolve, reject) => {
      const waiter: (typeof this.waiters)[number] = { resolve, reject, ...(signal ? { signal } : {}) };
      if (signal) {
        waiter.onAbort = () => {
          const index = this.waiters.indexOf(waiter);
          if (index >= 0) this.waiters.splice(index, 1);
          reject(signal.reason);
        };
        signal.addEventListener("abort", waiter.onAbort, { once: true });
      }
      this.waiters.push(waiter);
    });
  }

  release(): void {
    const waiter = this.waiters.shift();
    if (!waiter) {
      this.available += 1;
      return;
    }
    if (waiter.signal && waiter.onAbort) waiter.signal.removeEventListener("abort", waiter.onAbort);
    waiter.resolve();
  }

  async use<T>(operation: () => Promise<T>, signal?: AbortSignal): Promise<T> {
    await this.acquire(signal);
    try {
      return await operation();
    } finally {
      this.release();
    }
  }
}

export interface KabigonClientOptions {
  deadlineSeconds?: number;
  requestLimit?: number;
  browserLimit?: number;
  workerLimit?: number;
  fetchImplementation?: FetchImplementation;
  resolve?: PublicUrlResolver;
}

export class KabigonClient implements ResourceProvider, AsyncDisposable {
  readonly deadlineSeconds?: number;
  private readonly requestSlots: Semaphore;
  private readonly browserSlots: Semaphore;
  private readonly workerSlots: Semaphore;
  private readonly fetchImplementation: FetchImplementation;
  private readonly resolve?: PublicUrlResolver;
  private active = false;
  private impersPromise?: Promise<ImpersSession>;
  private browserPromise?: Promise<Browser>;

  constructor(options: KabigonClientOptions = {}) {
    if (options.deadlineSeconds !== undefined && options.deadlineSeconds <= 0) {
      throw new RangeError("deadlineSeconds must be positive");
    }
    const requestLimit = options.requestLimit ?? 8;
    const browserLimit = options.browserLimit ?? 2;
    const workerLimit = options.workerLimit ?? 2;
    if ([requestLimit, browserLimit, workerLimit].some((value) => value <= 0)) {
      throw new RangeError("concurrency limits must be positive");
    }
    this.deadlineSeconds = options.deadlineSeconds;
    this.requestSlots = new Semaphore(requestLimit);
    this.browserSlots = new Semaphore(browserLimit);
    this.workerSlots = new Semaphore(workerLimit);
    this.fetchImplementation = options.fetchImplementation ?? fetch;
    this.resolve = options.resolve;
  }

  start(): this {
    this.active = true;
    return this;
  }

  private checkActive(): void {
    if (!this.active) throw new Error(CLIENT_CONTEXT_REQUIRED);
  }

  async validateUrl(input: string | URL, signal?: AbortSignal): Promise<URL> {
    this.checkActive();
    return (await assertPublicUrl(input, { resolve: this.resolve, signal })).url;
  }

  fetch(input: string | URL, init?: RequestInit): Promise<Response> {
    this.checkActive();
    return safeFetch(input, init, {
      fetchImplementation: this.fetchImplementation,
      resolve: this.resolve,
      signal: init?.signal ?? undefined,
    });
  }

  async impersSession(): Promise<ImpersSession> {
    this.checkActive();
    if (!this.impersPromise) {
      this.impersPromise = import("impers").then(({ Session }) => new Session({ impersonate: "chrome" }));
    }
    return this.impersPromise;
  }

  async browser(): Promise<Browser> {
    this.checkActive();
    if (!this.browserPromise) {
      this.browserPromise = import("playwright")
        .then(({ chromium }) => chromium.launch({ headless: true }))
        .catch((error: unknown) => {
          this.browserPromise = undefined;
          throw error;
        });
    }
    return this.browserPromise;
  }

  runBrowser<T>(operation: () => Promise<T>): Promise<T> {
    return this.browserSlots.use(operation, admissionSignal());
  }

  runWorker<T>(operation: () => Promise<T>): Promise<T> {
    return this.workerSlots.use(operation, admissionSignal());
  }

  private admit(loaderName: string, operation: () => Promise<string>): Promise<string> {
    const kind = getLoaderDef(loaderName).resourceKind;
    const slots = kind === "browser" ? this.browserSlots : kind === "worker" ? this.workerSlots : this.requestSlots;
    return slots.use(operation, admissionSignal());
  }

  async loadUrlDetailed(url: string, signal?: AbortSignal): Promise<LoadResult> {
    this.checkActive();
    const deadlineAt =
      this.deadlineSeconds === undefined ? undefined : performance.now() + this.deadlineSeconds * 1_000;
    return withDeadline(deadlineAt, async () => {
      const timeoutSignal = admissionSignal();
      const validationSignal =
        signal && timeoutSignal ? AbortSignal.any([signal, timeoutSignal]) : (signal ?? timeoutSignal);
      await validateTarget(url, this.resolve, validationSignal);
      const chain = resolveLoadChain(url, {
        getFactory: (name) => () => createLoader(name, this),
        admit: (name, operation) => this.admit(name, operation),
      });
      return chain.loadDetailed(signal);
    });
  }

  async loadUrl(url: string, signal?: AbortSignal): Promise<string> {
    return (await this.loadUrlDetailed(url, signal)).content;
  }

  async close(): Promise<void> {
    if (!this.active) return;
    const [browser, impers] = await Promise.all([
      this.browserPromise?.catch(() => undefined),
      this.impersPromise?.catch(() => undefined),
    ]);
    this.browserPromise = undefined;
    this.impersPromise = undefined;
    this.active = false;
    const errors: unknown[] = [];
    if (browser) {
      try {
        await browser.close();
      } catch (error) {
        errors.push(error);
      }
    }
    if (impers) {
      try {
        await impers.close();
      } catch (error) {
        errors.push(error);
      }
    }
    if (errors.length > 0) throw errors[0];
  }

  async [Symbol.asyncDispose](): Promise<void> {
    await this.close();
  }
}

function admissionSignal(): AbortSignal | undefined {
  const remaining = remainingMilliseconds();
  return remaining === undefined ? undefined : AbortSignal.timeout(Math.max(1, Math.ceil(remaining)));
}

async function validateTarget(target: string, resolve?: PublicUrlResolver, signal?: AbortSignal): Promise<void> {
  if (isPdfTarget(target) && !target.startsWith("http://") && !target.startsWith("https://")) return;
  try {
    await assertPublicUrl(target, { resolve, signal });
  } catch (error) {
    if (error instanceof TypeError && error.message === "URL is invalid") throw new TypeError(INVALID_TARGET);
    throw error;
  }
}

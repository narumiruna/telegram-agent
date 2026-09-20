import type { Session as ImpersSession } from "impers";
import type { Browser } from "playwright";

export type { ImpersSession };

export interface ResourceProvider {
  validateUrl(input: string | URL, signal?: AbortSignal): Promise<URL>;
  fetch(input: string | URL, init?: RequestInit): Promise<Response>;
  impersSession(): Promise<ImpersSession>;
  impersProxy(): Promise<string>;
  browser(): Promise<Browser>;
  runBrowser<T>(operation: () => Promise<T>): Promise<T>;
  runWorker<T>(operation: () => Promise<T>): Promise<T>;
}

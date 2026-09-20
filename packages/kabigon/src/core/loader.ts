export interface Loader {
  load(url: string, signal?: AbortSignal): Promise<string>;
}

export type LoaderFactory = () => Loader | Promise<Loader>;

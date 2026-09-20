import { createServer, request as requestHttp, type ServerResponse } from "node:http";
import { connect as connectTcp, type LookupFunction, type Socket } from "node:net";

import { assertPublicUrl, type PublicUrlResolver } from "./network.js";

export interface PublicProxy {
  readonly url: string;
  close(): Promise<void>;
}

interface PublicProxyOptions {
  resolve?: PublicUrlResolver;
}

function failResponse(response: ServerResponse, error: unknown): void {
  if (response.headersSent) response.destroy(error instanceof Error ? error : undefined);
  else {
    response.writeHead(502, { "Content-Type": "text/plain" });
    response.end("Upstream connection failed");
  }
}

function pinnedLookup(address: { address: string; family: number }): LookupFunction {
  return (_hostname, options, callback) => {
    if (options.all) callback(null, [address]);
    else callback(null, address.address, address.family);
  };
}

export async function startPublicProxy(options: PublicProxyOptions = {}): Promise<PublicProxy> {
  const sockets = new Set<Socket>();
  const track = (socket: Socket) => {
    sockets.add(socket);
    socket.once("close", () => sockets.delete(socket));
  };
  const server = createServer((request, response) => {
    void (async () => {
      const target = new URL(request.url ?? "");
      if (target.protocol !== "http:") throw new TypeError("Proxy only accepts HTTP targets");
      const { addresses } = await assertPublicUrl(target, { resolve: options.resolve });
      const address = addresses[0];
      if (!address) throw new TypeError("Proxy target did not resolve to an address");
      const headers: Record<string, string | string[] | undefined> = { ...request.headers, host: target.host };
      delete headers["proxy-authorization"];
      delete headers["proxy-connection"];
      const upstream = requestHttp({
        protocol: "http:",
        hostname: target.hostname,
        port: target.port || 80,
        method: request.method,
        path: `${target.pathname}${target.search}`,
        headers,
        lookup: pinnedLookup(address),
      });
      upstream.on("response", (upstreamResponse) => {
        response.writeHead(
          upstreamResponse.statusCode ?? 502,
          upstreamResponse.statusMessage,
          upstreamResponse.headers,
        );
        upstreamResponse.pipe(response);
      });
      upstream.on("error", (error) => failResponse(response, error));
      request.pipe(upstream);
    })().catch((error: unknown) => failResponse(response, error));
  });

  server.on("connection", track);
  server.on("connect", (request, client, head) => {
    void (async () => {
      const target = new URL(`https://${request.url ?? ""}`);
      const port = Number(target.port || 443);
      if (!Number.isInteger(port) || port <= 0 || port > 65_535) throw new TypeError("Proxy target port is invalid");
      const { addresses } = await assertPublicUrl(new URL(`https://${target.hostname}/`), {
        resolve: options.resolve,
      });
      const address = addresses[0];
      if (!address) throw new TypeError("Proxy target did not resolve to an address");
      const upstream = connectTcp({ host: address.address, port, family: address.family });
      track(upstream);
      upstream.once("connect", () => {
        if (client.destroyed) {
          upstream.destroy();
          return;
        }
        client.write("HTTP/1.1 200 Connection Established\r\n\r\n");
        if (head.byteLength > 0) upstream.write(head);
        client.pipe(upstream).pipe(client);
      });
      upstream.once("error", () => {
        if (!client.destroyed) client.end("HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n");
      });
    })().catch(() => {
      if (!client.destroyed) client.end("HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n");
    });
  });

  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      server.off("error", reject);
      resolve();
    });
  });
  server.unref();
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Public proxy did not bind to a TCP port");
  return {
    url: `http://127.0.0.1:${address.port}`,
    async close() {
      for (const socket of sockets) socket.destroy();
      await new Promise<void>((resolve, reject) => {
        server.close((error) => (error ? reject(error) : resolve()));
      });
    },
  };
}

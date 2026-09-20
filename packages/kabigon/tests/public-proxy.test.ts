import { connect } from "node:net"

import { describe, expect, it } from "vitest"

import { startPublicProxy } from "../src/core/public-proxy.js"

describe("public proxy", () => {
  it("rejects CONNECT targets whose connection-time lookup is private", async () => {
    let resolutions = 0
    const proxy = await startPublicProxy({
      resolve: async () => {
        resolutions += 1
        return [{ address: "127.0.0.1", family: 4 }]
      },
    })
    try {
      const endpoint = new URL(proxy.url)
      const response = await new Promise<string>((resolve, reject) => {
        const socket = connect(Number(endpoint.port), endpoint.hostname)
        let content = ""
        socket.setEncoding("utf8")
        socket.on("connect", () => {
          socket.write(
            "CONNECT attacker.example:443 HTTP/1.1\r\nHost: attacker.example:443\r\n\r\n",
          )
        })
        socket.on("data", (chunk: string) => {
          content += chunk
        })
        socket.on("end", () => resolve(content))
        socket.on("error", reject)
      })
      expect(response).toContain("502 Bad Gateway")
      expect(resolutions).toBe(1)
    } finally {
      await proxy.close()
    }
  })
})

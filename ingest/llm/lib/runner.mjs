// runner.mjs — shared runtime for all llm/*.mjs scripts (D1.1 seam).
//
// Contract (enforced here so scripts cannot drift):
//   argv:  node <script> --payload <file>   (payload = JSON on disk or stdin)
//   stdout: ONE envelope {ok, schema_version, data|error}  — nothing else
//   stderr: logs
//   exit:   0 whenever an envelope was produced; 1 only on crash
//
// Mock seam (D7): ZAI_MOCK=<file> bypasses the SDK entirely and replays the
// fixture file — the injection point every unit test uses.
//
// SDK resolution: vendored ./vendor/z-ai-web-dev-sdk first (survives container
// recycles, D6/R9), then the global bun install path.
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { ok, fail } from "./envelope.mjs";

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

export function resolveSdkPath() {
  const candidates = [
    path.join(REPO_ROOT, "vendor", "z-ai-web-dev-sdk", "dist", "index.js"),
    "/home/z/.bun/install/global/node_modules/z-ai-web-dev-sdk/dist/index.js",
  ];
  return candidates;
}

export async function createClient() {
  if (process.env.ZAI_MOCK) {
    const raw = await readFile(process.env.ZAI_MOCK, "utf-8");
    const calls = JSON.parse(raw);
    let i = 0;
    return {
      mock: true,
      chat: {
        completions: {
          // Fixture: array of raw response strings (or {error}) replayed in order.
          create: async () => {
            const item = calls[i++] ?? calls[calls.length - 1];
            if (item && item.error) throw new Error(item.error);
            return { choices: [{ message: { content: item } }] };
          },
        },
      },
    };
  }
  let lastErr;
  for (const p of resolveSdkPath()) {
    try {
      const mod = await import(p);
      const ZAI = mod.default ?? mod;
      return await ZAI.create();
    } catch (e) {
      lastErr = e;
    }
  }
  throw new Error(`z-ai SDK not loadable (tried vendored + global): ${lastErr}`);
}

export async function readPayload() {
  const idx = process.argv.indexOf("--payload");
  if (idx !== -1 && process.argv[idx + 1]) {
    return JSON.parse(await readFile(process.argv[idx + 1], "utf-8"));
  }
  const stdin = await readFile(0, "utf-8").catch(() => "");
  if (!stdin.trim()) throw new Error("no payload: pass --payload <file> or stdin");
  return JSON.parse(stdin);
}

export async function main(handler) {
  let payload;
  try {
    payload = await readPayload();
  } catch (e) {
    console.log(JSON.stringify(fail("bad_payload", e.message, false)));
    return;
  }
  try {
    const data = await handler(payload);
    console.log(JSON.stringify(ok(data)));
  } catch (e) {
    const msg = String(e?.message ?? e);
    // Must stay in sync with jobsearch/llm.py _RETRYABLE_RE. Includes the
    // SDK's actual format ("API request failed with status 503") and common
    // fetch transients (CR-1-CODE F-04).
    const retryable = /429|rate|timeout|timed out|temporar|ECONNRESET|ETIMEDOUT|ECONNREFUSED|EPIPE|fetch failed|status 5\d\d/i.test(msg);
    console.log(JSON.stringify(fail(
      retryable ? "llm_retryable" : "llm_fatal", msg, retryable)));
  }
}

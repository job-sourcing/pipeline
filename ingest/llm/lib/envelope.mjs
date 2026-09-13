// envelope.mjs — the D1.1 cross-runtime envelope contract.
// One JSON object on stdout, logs to stderr, exit 0 whenever an envelope
// (success OR error) was produced. Nonzero exit = crash (no envelope).
export const SCHEMA_VERSION = "1";

export function ok(data) {
  return { ok: true, schema_version: SCHEMA_VERSION, data };
}

export function fail(code, message, retryable = false) {
  return {
    ok: false,
    schema_version: SCHEMA_VERSION,
    error: { code, message: String(message).slice(0, 500), retryable },
  };
}

// D1.2 salvage parsing — try, in order:
//   1. direct JSON.parse
//   2. fenced ```json ... ``` block
//   3. balanced-brace scan for the first parseable object
// Returns { value } on success or { error } describing the failure class.
export function salvageParseJson(text) {
  if (typeof text !== "string" || !text.trim()) {
    return { error: "empty_output" };
  }
  try {
    return { value: JSON.parse(text) };
  } catch { /* fall through */ }

  const fenced = text.match(/```(?:json)?\s*([\s\S]*?)```/i);
  if (fenced) {
    try {
      return { value: JSON.parse(fenced[1].trim()) };
    } catch { /* fall through */ }
  }

  // Balanced-brace scan: first { ... } block whose braces balance and parses.
  let depth = 0, start = -1, inStr = false, esc = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (esc) { esc = false; continue; }
    if (ch === "\\") { esc = true; continue; }
    if (ch === '"') { inStr = !inStr; continue; }
    if (inStr) continue;
    if (ch === "{") {
      if (depth === 0) start = i;
      depth++;
    } else if (ch === "}") {
      if (depth > 0) {
        depth--;
        if (depth === 0 && start >= 0) {
          try {
            return { value: JSON.parse(text.slice(start, i + 1)) };
          } catch { start = -1; }
        }
      }
    }
  }
  return { error: "unparseable_output" };
}

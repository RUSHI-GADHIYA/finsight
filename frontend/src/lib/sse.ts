// Server-sent events over POST. EventSource only does GET, so read the fetch body stream.

export interface SSEEvent {
  event: string;
  data: unknown;
}

/** Split a buffer into complete events; returns them plus the unfinished remainder. */
export function parseSSE(buffer: string): { events: SSEEvent[]; rest: string } {
  const normalized = buffer.replace(/\r\n?/g, "\n");
  const blocks = normalized.split("\n\n");
  const rest = blocks.pop() ?? "";
  const events: SSEEvent[] = [];
  for (const block of blocks) {
    let event = "message";
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith(":")) continue; // comment / keep-alive ping
      const colon = line.indexOf(":");
      const field = colon === -1 ? line : line.slice(0, colon);
      const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
      if (field === "event") event = value;
      else if (field === "data") data.push(value);
    }
    if (data.length === 0) continue;
    const raw = data.join("\n");
    let parsed: unknown = raw;
    try {
      parsed = JSON.parse(raw);
    } catch {
      // not JSON: keep the raw string
    }
    events.push({ event, data: parsed });
  }
  return { events, rest };
}

export async function streamSSE(
  url: string,
  body: unknown,
  onEvent: (e: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(url, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "text/event-stream",
    },
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok || !resp.body) {
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const json = (await resp.json()) as { detail?: unknown };
      if (json.detail)
        detail =
          typeof json.detail === "string"
            ? json.detail
            : JSON.stringify(json.detail);
    } catch {
      // keep the status line
    }
    throw new Error(detail);
  }
  const reader = resp.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    const { events, rest } = parseSSE(buffer + value);
    buffer = rest;
    events.forEach(onEvent);
  }
  parseSSE(buffer + "\n\n").events.forEach(onEvent);
}

import { describe, expect, it } from "vitest";

import { parseSSE } from "./sse";

describe("parseSSE", () => {
  it("parses complete events and keeps the unfinished remainder", () => {
    const { events, rest } = parseSSE(
      'event: run_started\ndata: {"thread_id": "t1"}\n\nevent: token\ndata: {"del',
    );
    expect(events).toEqual([
      { event: "run_started", data: { thread_id: "t1" } },
    ]);
    expect(rest).toBe('event: token\ndata: {"del');
  });

  it("handles CRLF line endings (sse-starlette default) and several events per chunk", () => {
    const { events, rest } = parseSSE(
      'event: a\r\ndata: 1\r\n\r\nevent: b\r\ndata: {"x": 2}\r\n\r\n',
    );
    expect(events).toEqual([
      { event: "a", data: 1 },
      { event: "b", data: { x: 2 } },
    ]);
    expect(rest).toBe("");
  });

  it("reassembles an event split across chunks", () => {
    const first = parseSSE('event: cost\ndata: {"call_');
    const second = parseSSE(first.rest + 'usd": 0.01}\n\n');
    expect(first.events).toEqual([]);
    expect(second.events).toEqual([
      { event: "cost", data: { call_usd: 0.01 } },
    ]);
  });

  it("ignores keep-alive comments and keeps non-JSON data as text", () => {
    const { events } = parseSSE(": ping\n\nevent: note\ndata: plain text\n\n");
    expect(events).toEqual([{ event: "note", data: "plain text" }]);
  });
});

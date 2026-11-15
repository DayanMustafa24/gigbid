import { expect, test } from "@playwright/test";
import { labelOf, segment } from "../../src/lib/claims";
import { budgetText, latestRunByJob, money, pct, titleCase } from "../../src/lib/format";
import { qs } from "../../src/lib/http";
import type { Claim, Run } from "../../src/lib/types";

const claim = (text: string, ids: string[] = ["e1"], label?: Claim["label"]): Claim => ({ text, evidence_ids: ids, label });

test.describe("segment()", () => {
  test("splits text around claims and keeps order", () => {
    const text = "Hello. I built a RAG pipeline. Thanks.";
    const { segments, unplaced } = segment(text, [claim("I built a RAG pipeline.")]);
    expect(segments.map((s) => s.text).join("")).toBe(text);
    expect(segments.filter((s) => s.claim)).toHaveLength(1);
    expect(segments[1]?.text).toBe("I built a RAG pipeline.");
    expect(unplaced).toEqual([]);
  });

  test("matches case-insensitively", () => {
    const { segments } = segment("i BUILT a thing", [claim("I built a thing")]);
    expect(segments).toHaveLength(1);
    expect(segments[0]?.claim).toBeTruthy();
  });

  test("a claim missing from the text is reported, never dropped", () => {
    const { segments, unplaced } = segment("Edited text only.", [claim("Original sentence")]);
    expect(unplaced).toHaveLength(1);
    expect(segments.every((s) => !s.claim)).toBe(true);
  });

  test("overlapping claims: the first one wins and text is never duplicated", () => {
    const text = "I shipped an OCR pipeline for invoices.";
    const { segments } = segment(text, [claim("I shipped an OCR pipeline"), claim("OCR pipeline for invoices")]);
    expect(segments.map((s) => s.text).join("")).toBe(text);
    expect(segments.filter((s) => s.claim)).toHaveLength(1);
  });

  test("empty claim text and empty claim list are safe", () => {
    expect(segment("abc", []).segments).toEqual([{ text: "abc" }]);
    expect(segment("abc", [claim("  ")]).unplaced).toEqual([]);
  });
});

test("labelOf prefers the verifier's label, then evidence presence", () => {
  expect(labelOf(claim("x", ["e"], "unsupported"))).toBe("unsupported");
  expect(labelOf(claim("x", ["e"]))).toBe("supported");
  expect(labelOf(claim("x", []))).toBe("unchecked");
});

test.describe("format", () => {
  test("money and pct", () => {
    expect(money(1500)).toBe("$1,500");
    expect(money(null)).toBe("–");
    expect(pct(0.756)).toBe("76%");
    expect(pct(undefined)).toBe("–");
  });
  test("budgetText handles ranges, single values and hourly", () => {
    expect(budgetText({ min: 100, max: 300, currency: "USD", type: "fixed" })).toBe("$100–$300");
    expect(budgetText({ min: 50, max: 50, currency: "USD", type: "hourly" })).toBe("$50/hr");
    expect(budgetText(null)).toBe("Budget n/a");
  });
  test("titleCase", () => expect(titleCase("awaiting_approval")).toBe("Awaiting approval"));
  test("latestRunByJob keeps the newest bid run per job (API lists newest first)", () => {
    const run = (id: string, subject_id: string, kind = "bid") => ({ id, subject_id, kind }) as unknown as Run;
    const m = latestRunByJob([run("new", "j1"), run("old", "j1"), run("c", "j2", "conversation")]);
    expect(m.get("j1")?.id).toBe("new");
    expect(m.has("j2")).toBe(false);
  });
});

test("qs drops empty values and encodes", () => {
  expect(qs({ a: "x y", b: "", c: undefined, d: 0 })).toBe("?a=x+y&d=0");
  expect(qs({})).toBe("");
});

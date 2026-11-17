import { describe, expect, it } from "vitest";
import {
  computeBarHeights,
  recentIsoDayRange,
  sparklineHasActivity,
  localDayOf,
  sourceEntryDay,
  sparklinesByThemeGroup,
} from "../../lib/derive";
import type { ClaimRow, SourceRow } from "../../lib/types";

describe("sparkline derive helpers", () => {
  it("computeBarHeights scales to maxHeight", () => {
    expect(computeBarHeights([0, 0], 16)).toEqual([0, 0]);
    expect(computeBarHeights([2, 4, 1], 20)).toEqual([10, 20, 5]);
  });

  it("sparklineHasActivity is false for all-zero series", () => {
    expect(sparklineHasActivity([])).toBe(false);
    expect(sparklineHasActivity([0, 0])).toBe(false);
    expect(sparklineHasActivity([0, 1])).toBe(true);
  });

  it("recentIsoDayRange returns consecutive ISO days", () => {
    expect(recentIsoDayRange(3, "2026-01-03")).toEqual([
      "2026-01-01",
      "2026-01-02",
      "2026-01-03",
    ]);
  });

  it("sparklinesByThemeGroup counts distinct sources by the week they entered", () => {
    const claim = (id: string, theme: number, sources: string[]): ClaimRow => ({
      claim_id: id,
      claim: id,
      status: "durable",
      sources,
      theme: `T${theme}`,
      theme_id: theme,
      // A crystallize date must NOT drive the chart.
      run_date: "2026-01-14",
    });
    const claims = [
      claim("a", 1, ["case-x", "case-y"]),
      claim("b", 1, ["case-x", "case-x2"]), // case-x2 = same bytes as case-x
      claim("c", 2, ["case-y", "case-old"]),
    ];
    const entries = new Map([
      ["case-x", { id: "sha-x", day: "2026-01-02" }],
      ["case-x2", { id: "sha-x", day: "2026-01-02" }],
      ["case-y", { id: "sha-y", day: "2026-01-10" }],
      ["case-old", { id: "sha-old", day: "2025-12-01" }],
    ]);
    const map = sparklinesByThemeGroup(claims, "2026-01-14", 2, entries);
    expect(map.get("i1")).toEqual([1, 1]);
    expect(map.get("i2")).toEqual([0, 1]);
  });

  it("sourceEntryDay prefers the intake day and never the re-run day", () => {
    const src = (o: Partial<SourceRow>): SourceRow =>
      ({ sha256: "s", status: "processed", ...o }) as SourceRow;
    expect(
      sourceEntryDay(
        src({
          captured_on: "2026-09-01",
          processed_on: "2026-11-15",
          content_date: "2026-08-01",
        }),
      ),
    ).toBe("2026-09-01");
    expect(
      sourceEntryDay(
        src({ processed_on: "2026-04-03", content_date: "2026-03-30" }),
      ),
    ).toBe("2026-04-03");
    expect(sourceEntryDay(src({ content_date: "2026-03-30" }))).toBe(
      "2026-03-30",
    );
    expect(sourceEntryDay(src({}))).toBeNull();
  });

  it("localDayOf reads built_at in the viewer timezone", () => {
    expect(localDayOf("2026-01-14")).toBe("2026-01-14");
    expect(localDayOf(undefined)).toBeUndefined();
    const d = new Date("2026-11-17T00:07:18Z");
    const want = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    expect(localDayOf("2026-11-17T00:07:18Z")).toBe(want);
  });
});

import { describe, expect, test } from "bun:test";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { destinations, highlights } from "./travelData";

const publicDir = join(import.meta.dir, "..", "..", "public");

describe("city highlights", () => {
  test("each city shows three highlights, each its own photo, none of them the city's backdrop", () => {
    for (const destination of destinations) {
      const spots = highlights[destination.id];
      expect(spots).toHaveLength(3);
      const photos = spots.map((spot) => spot.photo);
      expect(new Set(photos).size).toBe(3);
      expect(photos).not.toContain(destination.photo);
      for (const photo of photos) expect(existsSync(join(publicDir, photo))).toBe(true);
    }
  });
});

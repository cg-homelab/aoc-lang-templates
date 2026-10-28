import { expect, test } from "bun:test";

import { process as part1 } from "./part1";
import { process as part2 } from "./part2";

test("part1 example", async () => {
  const input = await Bun.file(new URL("example1.txt", import.meta.url)).text();
  expect(part1(input)).toBe("3");
});

test("part2 example", async () => {
  const input = await Bun.file(new URL("example2.txt", import.meta.url)).text();
  expect(part2(input)).toBe("9");
});

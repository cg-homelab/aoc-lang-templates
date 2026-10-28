import { process as part1 } from "./part1";
import { process as part2 } from "./part2";

const part = Bun.argv[2];
if (part !== "1" && part !== "2") {
  console.error("usage: bun main.ts <1|2>");
  process.exit(1);
}

const file = Bun.file("input.txt");
if (!(await file.exists())) {
  console.error("input.txt missing — paste your puzzle input");
  process.exit(1);
}
const input = await file.text();

console.log(part === "1" ? part1(input) : part2(input));

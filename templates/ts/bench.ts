import { process as part1 } from "./part1";
import { process as part2 } from "./part2";

// Bench protocol:
//   <cmd> bench <part> <input-path> <budget-ns> <min-iter> <max-iter>
// Runs process(input) while i < min-iter, or while i < max-iter and the loop
// has used less than budget-ns. stdout: one line per sample (ns); then, only
// if at least one iteration ran, "answer <N>\n" + exactly N bytes of answer
// (utf8, no trailing newline) + "\n"; then always a line "done". After
// flushing, the harness blocks until one line (or EOF) arrives on stdin so the
// runner can read its peak RSS while it is alive, then exits 0.
// Usage errors, unreadable input and answer mismatches go to stderr, exit 1.
const USAGE =
  "usage: <cmd> bench <1|2> <input-path> <budget-ns> <min-iter> <max-iter>";

let benchSink = 0;

export async function bench(args: string[]): Promise<number> {
  if (
    args.length !== 5 ||
    (args[0] !== "1" && args[0] !== "2") ||
    !/^\d+$/.test(args[2]) ||
    !/^\d+$/.test(args[3]) ||
    !/^\d+$/.test(args[4]) ||
    Number(args[4]) < Number(args[3])
  ) {
    console.error(USAGE);
    return 1;
  }
  const part = args[0];
  const path = args[1];
  const budgetNs = Number(args[2]);
  const minIter = Number(args[3]);
  const maxIter = Number(args[4]);

  const file = Bun.file(path);
  let input: string;
  if (!(await file.exists())) {
    console.error(`bench: cannot read ${path}`);
    return 1;
  }
  try {
    input = await file.text();
  } catch {
    console.error(`bench: cannot read ${path}`);
    return 1;
  }

  const processFn = part === "1" ? part1 : part2;
  // Allocated and touched up front, identically for any iteration count.
  const ns = new Float64Array(maxIter).fill(-1);

  let first = "";
  let i = 0;
  const tLoop = Bun.nanoseconds();
  while (i < minIter || (i < maxIter && Bun.nanoseconds() - tLoop < budgetNs)) {
    const t0 = Bun.nanoseconds();
    const raw: unknown = processFn(input);
    const t1 = Bun.nanoseconds();
    ns[i] = Math.trunc(t1 - t0);

    const ans = typeof raw === "string" ? raw : String(raw);

    if (i === 0) {
      first = ans;
    } else if (ans !== first) {
      console.error(
        `bench: answer mismatch at iteration ${i}: got ${JSON.stringify(ans)}, want ${JSON.stringify(first)}`,
      );
      return 1;
    }
    benchSink += ans.length;
    i++;
  }

  try {
    const w = Bun.stdout.writer();
    const CHUNK = 1000;
    for (let k = 0; k < i; k += CHUNK) {
      const end = Math.min(i, k + CHUNK);
      let chunk = "";
      for (let j = k; j < end; j++) chunk += `${ns[j]}\n`;
      w.write(chunk);
    }
    if (i > 0) {
      w.write(`answer ${Buffer.byteLength(first, "utf8")}\n${first}\n`);
    }
    w.write("done\n");
    await w.end();
  } catch {
    console.error("bench: cannot write to stdout");
    return 1;
  }
  for await (const _ of console) break;
  if (benchSink < 0) console.error("");
  return 0;
}

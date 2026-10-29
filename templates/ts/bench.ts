import { process as part1 } from "./part1";
import { process as part2 } from "./part2";

const USAGE = "usage: bun main.ts bench <1|2> <input-path> <iterations>";

let benchSink = 0;

function maxRssBytes(): number | null {
  try {
    const kb = process.resourceUsage?.().maxRSS;
    return typeof kb === "number" ? kb * 1024 : null;
  } catch {
    return null;
  }
}

function orNull(v: number | null): string {
  return v === null ? "null" : String(v);
}

export async function bench(args: string[]): Promise<number> {
  if (
    args.length !== 3 ||
    (args[0] !== "1" && args[0] !== "2") ||
    !/^\d+$/.test(args[2]) ||
    Number(args[2]) < 1
  ) {
    console.error(USAGE);
    return 1;
  }
  const part = args[0];
  const path = args[1];
  const iterations = Number(args[2]);

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
  // Allocated and touched up front so the buffer is not counted in the RSS delta.
  const ns = new Float64Array(iterations).fill(-1);
  const rssBefore = maxRssBytes();

  let first = "";
  for (let i = 0; i < iterations; i++) {
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
  }

  const rssAfter = maxRssBytes();

  const line =
    `{"schema":1,"part":${part},"iterations":${iterations},` +
    `"ns":[${Array.from(ns, (v) => String(v)).join(",")}],"answer":${JSON.stringify(first)},` +
    `"max_rss_before":${orNull(rssBefore)},"max_rss_after":${orNull(rssAfter)}}`;
  await Bun.write(Bun.stdout, line + "\n");
  if (benchSink < 0) console.error("");
  return 0;
}

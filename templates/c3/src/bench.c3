// In-process timing harness used by `just bench`. You never need to edit
// this file: it calls `part1::process` / `part2::process` and prints the
// samples for `bench_day.py` (harness protocol 2).
//
// Protocol: `<cmd> bench <1|2> <input-path> <budget-ns> <min-iter> <max-iter>`
//
// Runs `process` at least min-iter and at most max-iter times, stopping
// after min-iter once budget-ns has elapsed since the loop started. Prints
// one line per sample (nanoseconds, decimal), then, if at least one
// iteration ran, a line `answer <N>` followed by exactly the N answer bytes
// and a newline, then always a line `done`. It then waits for one line (or
// EOF) on stdin before exiting, so the runner can read this process's peak
// memory while it is still alive. Usage errors, unreadable input and answer
// mismatches go to stderr with exit code 1, nothing on stdout, no wait.
module aoc::bench;
import std::io;
import std::io::file;
import std::time;
import std::time::clock;
import aoc::part1;
import aoc::part2;

alias ProcessFn = fn String(Allocator, String);

// Keep-alive sink: the summed answer lengths are stored here with a volatile
// store after the loop, so the compiler cannot treat the answers as unused.
usz bench_sink;

// True when `s` is a non-empty run of ASCII digits (no sign, prefix or blanks).
fn bool is_decimal(String s)
{
	if (s.len == 0) return false;
	foreach (c : s) if (c < '0' || c > '9') return false;
	return true;
}

fn void is_decimal_cases() @test
{
	test::eq(is_decimal("0"), true);
	test::eq(is_decimal("1234567890"), true);
	test::eq(is_decimal(""), false);
	test::eq(is_decimal("-1"), false);
	test::eq(is_decimal("+1"), false);
	test::eq(is_decimal("0x10"), false);
	test::eq(is_decimal(" 1"), false);
}

// Writes the samples line by line through the libc-buffered stdout, then the
// length-prefixed answer when there is one and the `done` line, and flushes
// once at the end.
fn void? write_output(File* out, ulong[] ns, String answer)
{
	foreach (t : ns) io::fprintf(out, "%d\n", t)!;
	if (ns.len > 0)
	{
		io::fprintf(out, "answer %d\n", answer.len)!;
		out.write(answer)!;
		out.write_byte('\n')!;
	}
	out.write("done\n")!;
	out.flush()!;
}

fn int run(String[] args)
{
	String usage = "usage: __NAME__ bench <1|2> <input-path> <budget-ns> <min-iter> <max-iter>";
	if (args.len != 5) { io::eprintn(usage); return 1; }
	String part = args[0];
	if (part != "1" && part != "2") { io::eprintn(usage); return 1; }
	if (!is_decimal(args[2]) || !is_decimal(args[3]) || !is_decimal(args[4])) { io::eprintn(usage); return 1; }
	ulong? budget_ns = args[2].to_ulong();
	uint? min_iter = args[3].to_uint();
	uint? max_iter = args[4].to_uint();
	if (catch budget_ns, min_iter, max_iter) { io::eprintn(usage); return 1; }
	if (max_iter < min_iter) { io::eprintn(usage); return 1; }
	char[]? loaded = file::load(mem, args[1]);
	if (catch loaded) { io::eprintfn("bench: cannot read %s", args[1]); return 1; }
	String input = (String)loaded;

	// Resolve the part once so the selection is not inside the timed window.
	ProcessFn process = part == "1" ? &part1::process : &part2::process;
	// The sample buffer is allocated and touched before the loop, identically
	// whether the loop then runs zero times (the runner's idle baseline) or
	// many, so its pages cancel out of the runner's memory delta.
	ulong[] ns = mem::new_array(ulong, max_iter);
	foreach (&slot : ns) *slot = 0xFFFF_FFFF_FFFF_FFFF;
	String first;
	usz checksum;
	uint i = 0;
	Clock t_loop = clock::now();
	while (i < min_iter || (i < max_iter && (ulong)t_loop.to_now() < budget_ns))
	{
		Clock t0 = clock::now();
		String answer = process(mem, input);
		NanoDuration elapsed = t0.mark();
		ns[i] = (ulong)elapsed;
		checksum += answer.len;
		if (i == 0)
		{
			first = answer;
		}
		else
		{
			if (answer != first)
			{
				io::eprintfn("bench: answer mismatch at iteration %d: got %s, want %s", i, answer, first);
				return 1;
			}
			free(answer);
		}
		i++;
	}
	@volatile_store(bench_sink, checksum);
	if (catch write_output(io::stdout(), ns[:i], first))
	{
		io::eprintn("bench: cannot write to stdout");
		return 1;
	}
	// Park until the runner sends a line or closes stdin; content and errors
	// are ignored.
	String? line = io::readline(mem, io::stdin());
	if (try line) free(line);
	return 0;
}

// In-process timing harness used by `just bench`. You never need to edit
// this file: it calls `part1::process` / `part2::process` and prints one
// JSON line (schema 1) for `bench_day.py`.
//
// Protocol: `<cmd> bench <1|2> <input-path> <iterations>`
module aoc::bench;
import std::io;
import std::io::file;
import std::time;
import std::time::clock;
import libc;
import aoc::part1;
import aoc::part2;

struct Timeval { CLong tv_sec; CLong tv_usec; }
struct Rusage { Timeval ru_utime; Timeval ru_stime; CLong ru_maxrss; CLong[13] rest; }
extern fn CInt getrusage(CInt who, Rusage* usage);
alias ProcessFn = fn String(Allocator, String);
excuse UNAVAILABLE;

// Appends the JSON-escaped body of `s` (without surrounding quotes) to `out`.
fn void append_escaped(DString* out, String s)
{
	foreach (c : s)
	{
		switch (c)
		{
			case '"': out.append("\\\"");
			case '\\': out.append("\\\\");
			case '\n': out.append("\\n");
			case '\r': out.append("\\r");
			case '\t': out.append("\\t");
			default:
				if (c < 0x20) { out.appendf("\\u%04x", c); } else { out.append(c); }
		}
	}
}

fn void escape_cases() @test
{
	String[*] input = { "plain", "a\"b", "a\\b", "a\nb", "a\rb", "a\tb", "\x01", "\x1f", "åß→" };
	String[*] want = { "plain", "a\\\"b", "a\\\\b", "a\\nb", "a\\rb", "a\\tb", "\\u0001", "\\u001f", "åß→" };
	foreach (i, s : input)
	{
		DString out = dstring::new(mem);
		defer out.free();
		append_escaped(&out, s);
		test::eq(out.str_view(), want[i]);
	}
}

fn long? max_rss_bytes()
{
	$if env::POSIX && $$C_LONG_SIZE == 64:
		Rusage ru;
		if (getrusage(0, &ru) != 0 || ru.ru_maxrss < 0) return UNAVAILABLE~;
		$if env::DARWIN:
			return ru.ru_maxrss;
		$else
			return ru.ru_maxrss * 1024;
		$endif
	$else
		return UNAVAILABLE~;
	$endif
}

fn int run(String[] args)
{
	String usage = "usage: __NAME__ bench <1|2> <input-path> <iterations>";
	if (args.len != 3) { io::eprintn(usage); return 1; }
	String part = args[0];
	if (part != "1" && part != "2") { io::eprintn(usage); return 1; }
	uint iterations = args[2].to_uint() ?? 0;
	if (iterations == 0) { io::eprintn(usage); return 1; }
	char[]? loaded = file::load(mem, args[1]);
	if (catch loaded) { io::eprintfn("bench: cannot read %s", args[1]); return 1; }
	String input = (String)loaded;

	// Resolve the part once so the selection is not inside the timed window.
	ProcessFn process = part == "1" ? &part1::process : &part2::process;
	// The ns array is allocated and touched before the RSS snapshot so its
	// pages are not counted as growth caused by the solution. ru_maxrss is in
	// kB on Linux and bytes on macOS; non-POSIX / non-LP64 targets give null.
	ulong[] ns = mem::new_array(ulong, iterations);
	foreach (&slot : ns) *slot = 0xFFFF_FFFF_FFFF_FFFF;
	long? rss_before = max_rss_bytes();
	String first;
	usz checksum;
	for (uint i = 0; i < iterations; i++)
	{
		Clock t0 = clock::now();
		String answer = process(mem, input);
		NanoDuration elapsed = t0.mark();
		ns[i] = (ulong)elapsed;
		checksum += answer.len;
		if (i == 0) { first = answer; continue; }
		if (answer != first)
		{
			DString msg = dstring::new(mem);
			msg.appendf("bench: answer mismatch at iteration %d: got \"", i);
			append_escaped(&msg, answer);
			msg.append("\", want \"");
			append_escaped(&msg, first);
			msg.append("\"");
			io::eprintn(msg.str_view());
			return 1;
		}
		free(answer);
	}
	long? rss_after = max_rss_bytes();
	DString out = dstring::new(mem);
	out.appendf("{\"schema\":1,\"part\":%s,\"iterations\":%d,\"ns\":[", part, iterations);
	foreach (i, t : ns) { if (i > 0) out.append(','); out.appendf("%d", t); }
	out.append("],\"answer\":\"");
	append_escaped(&out, first);
	out.append("\",\"max_rss_before\":");
	if (try b = rss_before) { out.appendf("%d", b); } else { out.append("null"); }
	out.append(",\"max_rss_after\":");
	if (try a = rss_after) { out.appendf("%d", a); } else { out.append("null"); }
	out.append("}");
	io::printn(out.str_view());
	return 0;
}

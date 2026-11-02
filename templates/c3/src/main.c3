// C3 module names cannot contain '-', so every day shares the module names
// aoc / aoc::part1 / aoc::part2 / aoc::bench; the day's directory name only
// appears as the binary name (see project.json).
module aoc;
import std::io;
import std::io::file;
import aoc::part1;
import aoc::part2;
import aoc::bench;

// Tests live next to the code in part1.c3/part2.c3 (`c3c test` runs them).
fn int main(String[] args)
{
	if (args.len >= 2 && args[1] == "bench") return bench::run(args[2..]);
	String part = args.len == 2 ? args[1] : "";
	if (part != "1" && part != "2")
	{
		io::eprintn("usage: __NAME__ <1|2>");
		return 1;
	}
	char[]? input = file::load(mem, "input.txt");
	if (catch input)
	{
		io::eprintn("input.txt missing — paste your puzzle input");
		return 1;
	}
	defer free(input);
	String answer = part == "1" ? part1::process(mem, (String)input) : part2::process(mem, (String)input);
	defer free(answer);
	io::printn(answer);
	return 0;
}

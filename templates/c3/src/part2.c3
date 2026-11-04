module aoc::part2;
import std::io;

fn String process(Allocator allocator, String input)
{
	// Placeholder: total number of characters across all lines.
	usz total = 0;
	Splitter lines = input.trim_right("\n").tokenize_all("\n");
	while (try line = lines.next()) total += line.len;
	return string::format(allocator, "%d", total);
}

fn void example() @test
{
	String answer = process(mem, $embed("../example2.txt"));
	defer free(answer);
	test::eq(answer, "9");
}

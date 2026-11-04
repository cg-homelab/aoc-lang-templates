module aoc::part1;
import std::io;

fn String process(Allocator allocator, String input)
{
	// Placeholder: number of lines in the input.
	usz count = 0;
	Splitter lines = input.trim_right("\n").tokenize_all("\n");
	while (try lines.next()) count++;
	return string::format(allocator, "%d", count);
}

fn void example() @test
{
	String answer = process(mem, $embed("../example1.txt"));
	defer free(answer);
	test::eq(answer, "3");
}

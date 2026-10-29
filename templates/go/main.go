package main

import (
	"fmt"
	"os"

	"__NAME__/part1"
	"__NAME__/part2"
)

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: __NAME__ <1|2>")
		os.Exit(1)
	}

	if os.Args[1] == "bench" {
		os.Exit(runBench(os.Args[2:]))
	}

	input, err := os.ReadFile("input.txt")
	if err != nil {
		fmt.Fprintln(os.Stderr, "input.txt missing — paste your puzzle input")
		os.Exit(1)
	}

	switch os.Args[1] {
	case "1":
		fmt.Println(part1.Process(string(input)))
	case "2":
		fmt.Println(part2.Process(string(input)))
	default:
		fmt.Fprintln(os.Stderr, "usage: __NAME__ <1|2>")
		os.Exit(1)
	}
}

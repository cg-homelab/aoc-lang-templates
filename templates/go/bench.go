package main

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
	"time"

	"__NAME__/part1"
	"__NAME__/part2"
)

// Bench protocol:
//
//	<cmd> bench <part> <input-path> <budget-ns> <min-iter> <max-iter>
//
// The input file is read once. process() is then timed in a loop that runs at
// least min-iter times, and keeps going until max-iter samples are taken or
// budget-ns has elapsed. stdout carries one decimal nanosecond sample per
// line, then (only if at least one iteration ran) a line "answer <N>"
// followed by exactly N bytes of answer (no trailing newline) and a newline.
// A final "done" line is always written and flushed, and the process then
// blocks until one line (or EOF) arrives on stdin so the runner can read its
// peak RSS while it is still alive. Errors (usage, unreadable input, answer
// mismatch) go to stderr with exit 1, with nothing on stdout and no wait.

// benchSink keeps the answer-length checksum alive so the work is not elided.
var benchSink int

const benchUsage = "usage: __NAME__ bench <1|2> <input-path> <budget-ns> <min-iter> <max-iter>"

// runBench times process() in-process and prints samples on stdout.
func runBench(args []string) int {
	if len(args) != 5 || (args[0] != "1" && args[0] != "2") {
		fmt.Fprintln(os.Stderr, benchUsage)
		return 1
	}
	part := args[0]
	path := args[1]
	budgetNs, err1 := strconv.ParseUint(args[2], 10, 63)
	minIter, err2 := strconv.ParseUint(args[3], 10, 31)
	maxIter, err3 := strconv.ParseUint(args[4], 10, 31)
	if err1 != nil || err2 != nil || err3 != nil ||
		maxIter < minIter {
		fmt.Fprintln(os.Stderr, benchUsage)
		return 1
	}
	budget := time.Duration(budgetNs)

	data, err := os.ReadFile(path)
	if err != nil {
		fmt.Fprintf(os.Stderr, "bench: cannot read %s\n", path)
		return 1
	}
	input := string(data)

	process := part1.Process
	if part == "2" {
		process = part2.Process
	}

	// Allocated and touched up front, identically for zero or many iterations.
	ns := make([]int64, maxIter)
	for i := range ns {
		ns[i] = -1
	}

	first := ""
	i := 0
	tLoop := time.Now()
	for i < int(minIter) || (i < int(maxIter) && time.Since(tLoop) < budget) {
		start := time.Now()
		ans := process(input)
		ns[i] = time.Since(start).Nanoseconds()

		if i == 0 {
			first = ans
		} else if ans != first {
			fmt.Fprintf(os.Stderr, "bench: answer mismatch at iteration %d: got %q, want %q\n", i, ans, first)
			return 1
		}
		benchSink += len(ans)
		i++
	}

	w := bufio.NewWriter(os.Stdout)
	for _, v := range ns[:i] {
		w.WriteString(strconv.FormatInt(v, 10))
		w.WriteByte('\n')
	}
	if i > 0 {
		w.WriteString("answer " + strconv.Itoa(len(first)) + "\n")
		w.WriteString(first)
		w.WriteByte('\n')
	}
	w.WriteString("done\n")
	w.Flush()

	// Park until the runner has sampled our RSS; newline or EOF releases.
	_, _ = bufio.NewReader(os.Stdin).ReadString('\n')
	return 0
}

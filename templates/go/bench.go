package main

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"

	"__NAME__/part1"
	"__NAME__/part2"
)

// benchSink keeps the answer-length checksum alive so the work is not elided.
var benchSink int

const benchUsage = "usage: __NAME__ bench <1|2> <input-path> <iterations>"

// runBench times process() in-process and prints one JSON line on stdout.
func runBench(args []string) int {
	if len(args) != 3 || (args[0] != "1" && args[0] != "2") {
		fmt.Fprintln(os.Stderr, benchUsage)
		return 1
	}
	part := args[0]
	path := args[1]
	iterations, err := strconv.Atoi(args[2])
	if err != nil || iterations < 1 {
		fmt.Fprintln(os.Stderr, benchUsage)
		return 1
	}

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

	// Allocated and touched up front so the buffer is not counted in the RSS delta.
	ns := make([]int64, iterations)
	for i := range ns {
		ns[i] = -1
	}
	rssBefore, okBefore := maxRSSBytes()

	first := ""
	for i := 0; i < iterations; i++ {
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
	}

	rssAfter, okAfter := maxRSSBytes()

	var sb strings.Builder
	sb.WriteString(`{"schema":1,"part":`)
	sb.WriteString(part)
	sb.WriteString(`,"iterations":`)
	sb.WriteString(strconv.Itoa(iterations))
	sb.WriteString(`,"ns":[`)
	for i, v := range ns {
		if i > 0 {
			sb.WriteByte(',')
		}
		sb.WriteString(strconv.FormatInt(v, 10))
	}
	sb.WriteString(`],"answer":`)
	writeJSONString(&sb, first)
	sb.WriteString(`,"max_rss_before":`)
	writeRSS(&sb, rssBefore, okBefore)
	sb.WriteString(`,"max_rss_after":`)
	writeRSS(&sb, rssAfter, okAfter)
	sb.WriteString("}\n")

	fmt.Print(sb.String())
	return 0
}

func writeRSS(sb *strings.Builder, v int64, ok bool) {
	if !ok {
		sb.WriteString("null")
		return
	}
	sb.WriteString(strconv.FormatInt(v, 10))
}

// writeJSONString appends s as a JSON string literal (byte-wise escaping).
func writeJSONString(sb *strings.Builder, s string) {
	const hex = "0123456789abcdef"
	sb.WriteByte('"')
	for i := 0; i < len(s); i++ {
		c := s[i]
		switch {
		case c == '"':
			sb.WriteString(`\"`)
		case c == '\\':
			sb.WriteString(`\\`)
		case c == '\n':
			sb.WriteString(`\n`)
		case c == '\r':
			sb.WriteString(`\r`)
		case c == '\t':
			sb.WriteString(`\t`)
		case c < 0x20:
			sb.WriteString(`\u00`)
			sb.WriteByte(hex[c>>4])
			sb.WriteByte(hex[c&0xf])
		default:
			sb.WriteByte(c)
		}
	}
	sb.WriteByte('"')
}

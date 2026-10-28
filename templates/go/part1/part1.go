package part1

import (
	"strconv"
	"strings"
)

func Process(input string) string {
	// Placeholder: number of lines in the input.
	lines := strings.Split(strings.TrimRight(input, "\n"), "\n")
	return strconv.Itoa(len(lines))
}

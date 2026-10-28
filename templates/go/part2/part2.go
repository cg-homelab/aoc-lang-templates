package part2

import (
	"strconv"
	"strings"
)

func Process(input string) string {
	// Placeholder: total number of characters across all lines.
	total := 0
	for _, line := range strings.Split(strings.TrimRight(input, "\n"), "\n") {
		total += len(line)
	}
	return strconv.Itoa(total)
}

package part2

import (
	"os"
	"testing"
)

func TestExample(t *testing.T) {
	input, err := os.ReadFile("../example2.txt")
	if err != nil {
		t.Fatal(err)
	}
	if got, want := Process(string(input)), "9"; got != want {
		t.Errorf("Process(example2) = %q, want %q", got, want)
	}
}

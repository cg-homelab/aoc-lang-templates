package part1

import (
	"os"
	"testing"
)

func TestExample(t *testing.T) {
	input, err := os.ReadFile("../example1.txt")
	if err != nil {
		t.Fatal(err)
	}
	if got, want := Process(string(input)), "3"; got != want {
		t.Errorf("Process(example1) = %q, want %q", got, want)
	}
}

package main

import (
	"strings"
	"testing"
)

func TestWriteJSONString(t *testing.T) {
	cases := []struct{ in, want string }{
		{"plain", `"plain"`},
		{`a"b`, `"a\"b"`},
		{`a\b`, `"a\\b"`},
		{"a\nb", `"a\nb"`},
		{"a\rb", `"a\rb"`},
		{"a\tb", `"a\tb"`},
		{"\x01", `"\u0001"`},
		{"\x1f", `"\u001f"`},
		{"åß→", `"åß→"`},
	}
	for _, c := range cases {
		var sb strings.Builder
		writeJSONString(&sb, c.in)
		if got := sb.String(); got != c.want {
			t.Errorf("writeJSONString(%q) = %s, want %s", c.in, got, c.want)
		}
	}
}

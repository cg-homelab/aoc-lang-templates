//go:build !(linux || darwin)

package main

// maxRSSBytes is unsupported on this platform.
func maxRSSBytes() (int64, bool) {
	return 0, false
}

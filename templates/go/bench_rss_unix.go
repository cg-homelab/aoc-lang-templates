//go:build linux || darwin

package main

import (
	"runtime"
	"syscall"
)

// maxRSSBytes returns the peak resident set size of this process in bytes.
func maxRSSBytes() (int64, bool) {
	var ru syscall.Rusage
	if err := syscall.Getrusage(syscall.RUSAGE_SELF, &ru); err != nil {
		return 0, false
	}
	rss := int64(ru.Maxrss)
	if runtime.GOOS != "darwin" {
		rss *= 1024 // Linux reports kilobytes, macOS bytes
	}
	return rss, true
}

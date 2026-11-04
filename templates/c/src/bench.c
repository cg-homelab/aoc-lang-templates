/* In-process timing harness used by `just bench`. You never need to edit
 * this file: it calls `part1_process` / `part2_process` and prints one
 * JSON line (schema 1) for `bench_day.py`.
 *
 * Protocol: `<cmd> bench <1|2> <input-path> <iterations>`
 */

#define _POSIX_C_SOURCE 200809L
#ifdef __APPLE__
#define _DARWIN_C_SOURCE
#endif

#include <errno.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#if defined(__unix__) || defined(__APPLE__)
#include <sys/resource.h>
#endif

#include "bench.h"
#include "part1.h"
#include "part2.h"

#define USAGE "usage: __NAME__ bench <1|2> <input-path> <iterations>\n"

/* Keeps the work observable so the compiler cannot optimise it away. */
static volatile size_t bench_sink;

char *bench_read_file(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    size_t cap = 4096, len = 0;
    char *buf = malloc(cap);
    if (!buf) {
        fclose(f);
        return NULL;
    }
    for (;;) {
        if (len + 1 >= cap) {
            cap *= 2;
            char *grown = realloc(buf, cap);
            if (!grown) {
                free(buf);
                fclose(f);
                return NULL;
            }
            buf = grown;
        }
        size_t n = fread(buf + len, 1, cap - len - 1, f);
        len += n;
        if (n == 0) break;
    }
    int failed = ferror(f);
    fclose(f);
    if (failed) {
        free(buf);
        return NULL;
    }
    buf[len] = '\0';
    return buf;
}

char *bench_json_escape(const char *s) {
    size_t len = strlen(s);
    char *out = malloc(len * 6 + 1);
    if (!out) return NULL;
    char *p = out;
    for (size_t i = 0; i < len; i++) {
        unsigned char c = (unsigned char)s[i];
        switch (c) {
        case '"': *p++ = '\\'; *p++ = '"'; break;
        case '\\': *p++ = '\\'; *p++ = '\\'; break;
        case '\n': *p++ = '\\'; *p++ = 'n'; break;
        case '\r': *p++ = '\\'; *p++ = 'r'; break;
        case '\t': *p++ = '\\'; *p++ = 't'; break;
        default:
            if (c < 0x20) p += snprintf(p, 7, "\\u%04x", (unsigned)c);
            else *p++ = (char)c;
        }
    }
    *p = '\0';
    return out;
}

/* Peak RSS in bytes; returns 0 on success, -1 when unavailable. */
static int max_rss_bytes(uint64_t *out) {
#if defined(__unix__) || defined(__APPLE__)
    struct rusage usage;
    if (getrusage(RUSAGE_SELF, &usage) != 0 || usage.ru_maxrss < 0) return -1;
    uint64_t maxrss = (uint64_t)usage.ru_maxrss;
#ifdef __APPLE__
    *out = maxrss; /* macOS reports bytes */
#else
    *out = maxrss * 1024; /* Linux reports kilobytes */
#endif
    return 0;
#else
    (void)out;
    return -1;
#endif
}

static void print_rss(int ok, uint64_t v) {
    if (ok == 0) printf("%" PRIu64, v);
    else printf("null");
}

static uint64_t now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000u + (uint64_t)ts.tv_nsec;
}

int bench_run(int argc, char **argv) {
    if (argc != 3) {
        fputs(USAGE, stderr);
        return 1;
    }
    char *(*process)(const char *);
    if (strcmp(argv[0], "1") == 0) process = part1_process;
    else if (strcmp(argv[0], "2") == 0) process = part2_process;
    else {
        fputs(USAGE, stderr);
        return 1;
    }
    const char *part = argv[0];
    const char *path = argv[1];
    char *end = NULL;
    errno = 0;
    long iters = strtol(argv[2], &end, 10);
    if (argv[2][0] == '\0' || *end != '\0' || errno != 0 || iters <= 0 || (unsigned long)iters > UINT32_MAX) {
        fputs(USAGE, stderr);
        return 1;
    }
    size_t iterations = (size_t)iters;

    char *input = bench_read_file(path);
    if (!input) {
        fprintf(stderr, "bench: cannot read %s\n", path);
        return 1;
    }

    /* Allocate and touch the sample buffer up front so its pages are not
     * counted in the RSS delta. */
    uint64_t *ns = malloc(iterations * sizeof *ns);
    if (!ns) {
        fputs("bench: out of memory\n", stderr);
        free(input);
        return 1;
    }
    for (size_t i = 0; i < iterations; i++) ns[i] = UINT64_MAX;

    uint64_t rss_before = 0, rss_after = 0;
    int rss_before_ok = max_rss_bytes(&rss_before);
    char *first = NULL;
    size_t checksum = 0;
    for (size_t i = 0; i < iterations; i++) {
        uint64_t t0 = now_ns();
        char *answer = process(input);
        uint64_t t1 = now_ns();
        ns[i] = t1 - t0;
        if (!answer) {
            fprintf(stderr, "bench: process failed at iteration %zu\n", i);
            free(first);
            free(ns);
            free(input);
            return 1;
        }
        checksum += strlen(answer);
        if (!first) {
            first = answer;
        } else {
            int same = strcmp(first, answer) == 0;
            if (!same) {
                char *got = bench_json_escape(answer);
                char *want = bench_json_escape(first);
                fprintf(stderr,
                        "bench: answer mismatch at iteration %zu: got \"%s\", want \"%s\"\n",
                        i, got ? got : "", want ? want : "");
                free(got);
                free(want);
                free(answer);
                free(first);
                free(ns);
                free(input);
                return 1;
            }
            free(answer);
        }
    }
    bench_sink = checksum;
    int rss_after_ok = max_rss_bytes(&rss_after);

    char *escaped = bench_json_escape(first);
    printf("{\"schema\":1,\"part\":%s,\"iterations\":%zu,\"ns\":[", part, iterations);
    for (size_t i = 0; i < iterations; i++) {
        if (i > 0) putchar(',');
        printf("%" PRIu64, ns[i]);
    }
    printf("],\"answer\":\"%s\",\"max_rss_before\":", escaped ? escaped : "");
    print_rss(rss_before_ok, rss_before);
    printf(",\"max_rss_after\":");
    print_rss(rss_after_ok, rss_after);
    printf("}\n");

    free(escaped);
    free(first);
    free(ns);
    free(input);
    return 0;
}

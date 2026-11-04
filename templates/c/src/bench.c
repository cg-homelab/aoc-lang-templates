/* In-process timing harness used by `just bench`. You never need to edit
 * this file: it calls `part1_process` / `part2_process` and prints raw
 * samples for the runner (`scripts/bench_day.py`).
 *
 * Protocol: `<cmd> bench <1|2> <input-path> <budget-ns> <min-iter> <max-iter>`
 * Runs process(input) at least min-iter times, then keeps going until
 * budget-ns has elapsed or max-iter is reached. Stdout: one nanosecond
 * sample per line, then (only if at least one iteration ran) the line
 * "answer <N>" followed by the answer text (N bytes) and a newline, then
 * the line "done". After "done" the harness waits for one line (or EOF) on
 * stdin before exiting 0, so the runner can read its peak RSS while it is
 * alive. Errors go to stderr with exit code 1, nothing on stdout, no wait.
 */

#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

#include "bench.h"
#include "part1.h"
#include "part2.h"

#define USAGE "usage: __NAME__ bench <1|2> <input-path> <budget-ns> <min-iter> <max-iter>\n"

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

static uint64_t now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000u + (uint64_t)ts.tv_nsec;
}

/* Strict non-negative decimal parse: digits only, no sign/space/empty. */
static int parse_u64(const char *s, uint64_t *out) {
    if (s[0] < '0' || s[0] > '9') return -1;
    char *end = NULL;
    errno = 0;
    unsigned long long v = strtoull(s, &end, 10);
    if (*end != '\0' || errno != 0) return -1;
    *out = (uint64_t)v;
    return 0;
}

int bench_run(int argc, char **argv) {
#ifdef _WIN32
    /* Binary stdout so "\n" is not expanded to "\r\n" (answer <N> is a byte count). */
    _setmode(_fileno(stdout), _O_BINARY);
#endif
    if (argc != 5) {
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
    const char *path = argv[1];
    uint64_t budget_ns, min_u, max_u;
    if (parse_u64(argv[2], &budget_ns) != 0 || parse_u64(argv[3], &min_u) != 0 ||
        parse_u64(argv[4], &max_u) != 0 || min_u > UINT32_MAX || max_u > UINT32_MAX ||
        max_u < min_u) {
        fputs(USAGE, stderr);
        return 1;
    }
    size_t min_iter = (size_t)min_u, max_iter = (size_t)max_u;

    char *input = bench_read_file(path);
    if (!input) {
        fprintf(stderr, "bench: cannot read %s\n", path);
        return 1;
    }

    /* Allocate and touch the sample buffer up front, identically whether the
     * loop runs zero or many times (the runner's idle baseline relies on it). */
    size_t slots = max_iter ? max_iter : 1;
    uint64_t *ns = malloc(slots * sizeof *ns);
    if (!ns) {
        fputs("bench: out of memory\n", stderr);
        free(input);
        return 1;
    }
    for (size_t i = 0; i < slots; i++) ns[i] = UINT64_MAX;

    char *first = NULL;
    size_t checksum = 0;
    size_t i = 0;
    uint64_t t_loop = now_ns();
    while (i < min_iter || (i < max_iter && now_ns() - t_loop < budget_ns)) {
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
                fprintf(stderr, "bench: answer mismatch at iteration %zu: got \"", i);
                fputs(answer, stderr);
                fputs("\", want \"", stderr);
                fputs(first, stderr);
                fputs("\"\n", stderr);
                free(answer);
                free(first);
                free(ns);
                free(input);
                return 1;
            }
            free(answer);
        }
        i++;
    }
    bench_sink = checksum;

    for (size_t k = 0; k < i; k++) printf("%" PRIu64 "\n", ns[k]);
    if (i > 0) {
        printf("answer %zu\n", strlen(first));
        fputs(first, stdout);
        fputc('\n', stdout);
    }
    fputs("done\n", stdout);
    fflush(stdout);

    /* Park until the runner has sampled our peak RSS: one stdin line (or
     * EOF) releases us. */
    int c;
    while ((c = getchar()) != EOF && c != '\n') {
    }

    free(first);
    free(ns);
    free(input);
    return 0;
}

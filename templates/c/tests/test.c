#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "bench.h"
#include "part1.h"
#include "part2.h"

static int checks = 0;
static int failures = 0;

#define CHECK_STR(got, want)                                                       \
    do {                                                                           \
        const char *got_ = (got);                                                  \
        const char *want_ = (want);                                                \
        checks++;                                                                  \
        if (!got_ || strcmp(got_, want_) != 0) {                                   \
            failures++;                                                            \
            fprintf(stderr, "FAIL %s:%d: %s = \"%s\", want \"%s\"\n", __FILE__,    \
                    __LINE__, #got, got_ ? got_ : "(null)", want_);                \
        }                                                                          \
    } while (0)

static void check_escape(const char *in, const char *want) {
    char *got = bench_json_escape(in);
    CHECK_STR(got, want);
    free(got);
}

int main(void) {
    char *ex1 = bench_read_file("example1.txt");
    char *ex2 = bench_read_file("example2.txt");
    checks++;
    if (!ex1 || !ex2) {
        failures++;
        fprintf(stderr, "FAIL: cannot read example1.txt / example2.txt\n");
        printf("%d checks, %d failures\n", checks, failures);
        return 1;
    }

    char *a1 = part1_process(ex1);
    CHECK_STR(a1, "3");
    free(a1);
    char *a2 = part2_process(ex2);
    CHECK_STR(a2, "9");
    free(a2);
    free(ex1);
    free(ex2);

    check_escape("plain", "plain");
    check_escape("a\"b", "a\\\"b");
    check_escape("a\\b", "a\\\\b");
    check_escape("a\nb", "a\\nb");
    check_escape("a\rb", "a\\rb");
    check_escape("a\tb", "a\\tb");
    check_escape("\x01", "\\u0001");
    check_escape("\x1f", "\\u001f");
    check_escape("åß→", "åß→");

    printf("%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}

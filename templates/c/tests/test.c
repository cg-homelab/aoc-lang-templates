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

    printf("%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}

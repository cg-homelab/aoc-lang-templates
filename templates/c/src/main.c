#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "bench.h"
#include "part1.h"
#include "part2.h"

int main(int argc, char **argv) {
    if (argc >= 2 && strcmp(argv[1], "bench") == 0) return bench_run(argc - 2, argv + 2);

    char *(*process)(const char *) = NULL;
    if (argc == 2 && strcmp(argv[1], "1") == 0) process = part1_process;
    else if (argc == 2 && strcmp(argv[1], "2") == 0) process = part2_process;
    else {
        fprintf(stderr, "usage: __NAME__ <1|2>\n");
        return 1;
    }

    char *input = bench_read_file("input.txt");
    if (!input) {
        fprintf(stderr, "input.txt missing — paste your puzzle input\n");
        return 1;
    }

    char *answer = process(input);
    free(input);
    if (!answer) return 1;
    printf("%s\n", answer);
    free(answer);
    return 0;
}

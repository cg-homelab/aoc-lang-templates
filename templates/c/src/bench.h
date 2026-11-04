#ifndef BENCH_H
#define BENCH_H

/* Entry point for `<cmd> bench <1|2> <input-path> <budget-ns> <min-iter> <max-iter>`.
 * Prints samples, "answer <N>" + answer (if any iteration ran) and "done",
 * then waits for one stdin line (or EOF) before returning 0. */
int bench_run(int argc, char **argv);

/* Reads a whole file into a malloc'd NUL-terminated buffer; NULL on failure. */
char *bench_read_file(const char *path);

#endif

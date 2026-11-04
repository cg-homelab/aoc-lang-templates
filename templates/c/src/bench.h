#ifndef BENCH_H
#define BENCH_H

/* Entry point for `<cmd> bench <1|2> <input-path> <iterations>`. */
int bench_run(int argc, char **argv);

/* Reads a whole file into a malloc'd NUL-terminated buffer; NULL on failure. */
char *bench_read_file(const char *path);

/* JSON string body escaping (without the surrounding quotes); malloc'd. */
char *bench_json_escape(const char *s);

#endif

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "part1.h"

char *part1_process(const char *input) {
    // Placeholder: number of lines in the input.
    size_t len = strlen(input);
    while (len > 0 && input[len - 1] == '\n') len--;
    size_t count = 1;
    for (size_t i = 0; i < len; i++) {
        if (input[i] == '\n') count++;
    }
    char *out = malloc(32);
    if (!out) return NULL;
    snprintf(out, 32, "%zu", count);
    return out;
}

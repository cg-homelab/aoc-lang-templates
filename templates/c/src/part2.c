#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "part2.h"

char *part2_process(const char *input) {
    // Placeholder: total number of characters across all lines.
    size_t len = strlen(input);
    while (len > 0 && input[len - 1] == '\n') len--;
    size_t count = 0;
    for (size_t i = 0; i < len; i++) {
        if (input[i] != '\n') count++;
    }
    char *out = malloc(32);
    if (!out) return NULL;
    snprintf(out, 32, "%zu", count);
    return out;
}

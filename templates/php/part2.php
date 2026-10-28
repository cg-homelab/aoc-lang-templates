<?php

declare(strict_types=1);

function part2(string $input): string
{
    // Placeholder: total number of characters across all lines.
    $total = 0;
    foreach (explode("\n", rtrim($input, "\n")) as $line) {
        $total += strlen($line);
    }
    return (string) $total;
}

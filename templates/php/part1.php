<?php

declare(strict_types=1);

function part1(string $input): string
{
    // Placeholder: number of lines in the input.
    $lines = explode("\n", rtrim($input, "\n"));
    return (string) count($lines);
}

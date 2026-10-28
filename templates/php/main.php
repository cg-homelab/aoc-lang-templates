<?php

declare(strict_types=1);

require __DIR__ . '/part1.php';
require __DIR__ . '/part2.php';

$part = $argv[1] ?? '';
if (!in_array($part, ['1', '2'], true)) {
    fwrite(STDERR, "usage: php main.php <1|2>\n");
    exit(1);
}

$input = @file_get_contents(__DIR__ . '/input.txt');
if ($input === false) {
    fwrite(STDERR, "input.txt missing — paste your puzzle input\n");
    exit(1);
}

echo ($part === '1' ? part1($input) : part2($input)) . "\n";

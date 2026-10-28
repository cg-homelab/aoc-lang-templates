<?php

declare(strict_types=1);

require __DIR__ . '/part1.php';
require __DIR__ . '/part2.php';

$failures = 0;

function check(string $name, string $actual, string $expected): void
{
    global $failures;
    if ($actual === $expected) {
        echo "ok   $name\n";
    } else {
        echo "FAIL $name: got $actual, want $expected\n";
        $failures++;
    }
}

check('part1 example', part1(file_get_contents(__DIR__ . '/example1.txt')), '3');
check('part2 example', part2(file_get_contents(__DIR__ . '/example2.txt')), '9');

exit($failures === 0 ? 0 : 1);

<?php

declare(strict_types=1);

// In-process timing harness: `php main.php bench <1|2> <input-path> <iterations>`.
// This file must NOT require part1.php / part2.php: main.php has already required
// them before dispatching here, and a second require would redeclare the functions.

function bench_max_rss_bytes(): ?int
{
    if (PHP_OS_FAMILY === 'Windows') {
        return null;
    }
    $usage = getrusage();
    if ($usage === false || !isset($usage['ru_maxrss'])) {
        return null;
    }
    $rss = (int) $usage['ru_maxrss'];
    return PHP_OS_FAMILY === 'Darwin' ? $rss : $rss * 1024;
}

function bench_json_string(string $s): string
{
    $out = '"';
    $len = strlen($s);
    for ($i = 0; $i < $len; $i++) {
        $c = $s[$i];
        $o = ord($c);
        if ($c === '"') {
            $out .= '\\"';
        } elseif ($c === '\\') {
            $out .= '\\\\';
        } elseif ($c === "\n") {
            $out .= '\\n';
        } elseif ($c === "\r") {
            $out .= '\\r';
        } elseif ($c === "\t") {
            $out .= '\\t';
        } elseif ($o < 0x20) {
            $out .= sprintf('\\u%04x', $o);
        } else {
            $out .= $c;
        }
    }
    return $out . '"';
}

/** @param list<string> $args */
function bench_main(array $args): int
{
    if (
        count($args) !== 3
        || !in_array($args[0], ['1', '2'], true)
        || !ctype_digit($args[2])
        || (int) $args[2] < 1
    ) {
        fwrite(STDERR, "usage: php main.php bench <1|2> <input-path> <iterations>\n");
        return 1;
    }
    $part = $args[0];
    $path = $args[1];
    $iterations = (int) $args[2];

    $input = @file_get_contents($path);
    if ($input === false) {
        fwrite(STDERR, "bench: cannot read $path\n");
        return 1;
    }

    // Allocated up front so the buffer is not counted in the RSS delta.
    $ns = array_fill(0, $iterations, 0);
    $rssBefore = bench_max_rss_bytes();

    $first = '';
    $checksum = 0;
    for ($i = 0; $i < $iterations; $i++) {
        $t0 = (int) hrtime(true);
        $ans = $part === '1' ? part1($input) : part2($input);
        $t1 = (int) hrtime(true);
        $ns[$i] = $t1 - $t0;

        if ($i === 0) {
            $first = $ans;
        } elseif ($ans !== $first) {
            fwrite(STDERR, "bench: answer mismatch at iteration $i: got \"$ans\", want \"$first\"\n");
            return 1;
        }
        $checksum += strlen($ans);
    }

    $rssAfter = bench_max_rss_bytes();

    $line = '{"schema":1,"part":' . $part
        . ',"iterations":' . $iterations
        . ',"ns":[' . implode(',', $ns) . ']'
        . ',"answer":' . bench_json_string($first)
        . ',"max_rss_before":' . ($rssBefore === null ? 'null' : (string) $rssBefore)
        . ',"max_rss_after":' . ($rssAfter === null ? 'null' : (string) $rssAfter)
        . '}';
    fwrite(STDOUT, $line . "\n");
    return $checksum >= 0 ? 0 : 1;
}

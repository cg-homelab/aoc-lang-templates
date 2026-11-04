<?php

declare(strict_types=1);

// In-process timing harness:
//   `php main.php bench <1|2> <input-path> <budget-ns> <min-iter> <max-iter>`
// Runs the part at least min-iter times, then keeps going until budget-ns has
// elapsed or max-iter is reached. Stdout: one decimal ns sample per line, then,
// only if at least one iteration ran, `answer <byte-length>` and the answer on
// the next line, then always a `done` line. After `done` the harness flushes and
// blocks on one stdin line (newline or EOF) so the runner can read its peak RSS
// from /proc while it is alive; errors exit 1 immediately without parking.
// This file must NOT require part1.php / part2.php: main.php has already required
// them before dispatching here, and a second require would redeclare the functions.

/** @param list<string> $args */
function bench_main(array $args): int
{
    ini_set('display_errors', 'stderr');
    set_exception_handler(static function (Throwable $e): void {
        fwrite(STDERR, 'bench: ' . $e->getMessage() . "\n");
        exit(1);
    });

    if (
        count($args) !== 5
        || !in_array($args[0], ['1', '2'], true)
        || !ctype_digit($args[2])
        || !ctype_digit($args[3])
        || !ctype_digit($args[4])
        || (int) $args[4] < (int) $args[3]
    ) {
        fwrite(STDERR, "usage: php main.php bench <1|2> <input-path> <budget-ns> <min-iter> <max-iter>\n");
        return 1;
    }
    $part = $args[0];
    $path = $args[1];
    $budgetNs = (int) $args[2];
    $minIter = (int) $args[3];
    $maxIter = (int) $args[4];

    if (!is_file($path) || ($input = @file_get_contents($path)) === false) {
        fwrite(STDERR, "bench: cannot read $path\n");
        return 1;
    }

    // Allocated and touched up front, identically whether the loop runs 0 or many times.
    $ns = array_fill(0, $maxIter, 0);

    $first = '';
    $i = 0;
    $tLoop = (int) hrtime(true);
    while ($i < $minIter || ($i < $maxIter && (int) hrtime(true) - $tLoop < $budgetNs)) {
        $t0 = (int) hrtime(true);
        $raw = $part === '1' ? part1($input) : part2($input);
        $t1 = (int) hrtime(true);
        $ans = (string) $raw;
        $ns[$i] = $t1 - $t0;

        if ($i === 0) {
            $first = $ans;
        } elseif ($ans !== $first) {
            fwrite(STDERR, "bench: answer mismatch at iteration $i: got \"$ans\", want \"$first\"\n");
            return 1;
        }
        $i++;
    }

    for ($off = 0; $off < $i; $off += 1000) {
        fwrite(STDOUT, implode("\n", array_slice($ns, $off, min(1000, $i - $off))) . "\n");
    }
    if ($i > 0) {
        fwrite(STDOUT, 'answer ' . strlen($first) . "\n" . $first . "\n");
    }
    fwrite(STDOUT, "done\n");
    fflush(STDOUT);
    fgets(STDIN);
    return 0;
}

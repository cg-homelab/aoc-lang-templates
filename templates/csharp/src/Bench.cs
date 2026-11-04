// In-process timing harness used by `just bench`. You never need to edit
// this file: it calls Part1.Process / Part2.Process and prints one line per
// timed sample (ns), then, only if at least one sample was taken, a line
// `answer <N>` (N = UTF-8 byte length of the answer) followed by the answer
// and a newline, then a `done` line. After `done` it blocks on one stdin line
// (newline or EOF) so the runner can read its peak RSS before it exits.
//
// Protocol: <cmd> bench <1|2> <input-path> <budget-ns> <min-iter> <max-iter>
// Runs at least min-iter and at most max-iter iterations, stopping early once
// budget-ns has elapsed (after min-iter is reached).
using System.Diagnostics;
using System.Globalization;
using System.Runtime.CompilerServices;
using System.Text;

namespace Day__DAY__;

public static class Bench
{
    private const string Usage =
        "usage: __NAME__ bench <1|2> <input-path> <budget-ns> <min-iter> <max-iter>";

    public static int Run(string[] args)
    {
        if (args.Length != 5 || (args[0] != "1" && args[0] != "2")
            || !ulong.TryParse(args[2], NumberStyles.None, CultureInfo.InvariantCulture, out var budgetNs)
            || !int.TryParse(args[3], NumberStyles.None, CultureInfo.InvariantCulture, out var minIter)
            || !int.TryParse(args[4], NumberStyles.None, CultureInfo.InvariantCulture, out var maxIter)
            || maxIter < minIter)
        {
            Console.Error.WriteLine(Usage);
            return 1;
        }
        var part = args[0];
        var path = args[1];
        Func<string, string> process = part == "1" ? Part1.Process : Part2.Process;

        string input;
        try
        {
            input = File.ReadAllText(path);
        }
        catch (Exception)
        {
            Console.Error.WriteLine("bench: cannot read " + path);
            return 1;
        }

        // Allocate and touch the sample buffer up front, identically whether
        // the loop runs zero or many times.
        var ns = new long[maxIter];
        Array.Fill(ns, -1L);
        var freq = Stopwatch.Frequency;
        string? first = null;
        long checksum = 0;
        var i = 0;
        var tLoop = Stopwatch.GetTimestamp();
        while (i < minIter || (i < maxIter && ElapsedNs(tLoop, freq) < budgetNs))
        {
            var t0 = Stopwatch.GetTimestamp();
            var answer = Call(process, input);
            var dt = Stopwatch.GetTimestamp() - t0;
            ns[i] = dt / freq * 1_000_000_000L + (dt % freq) * 1_000_000_000L / freq;
            checksum += answer.Length;
            if (first is null)
            {
                first = answer;
            }
            else if (!string.Equals(answer, first, StringComparison.Ordinal))
            {
                Console.Error.WriteLine("bench: answer mismatch at iteration "
                    + i.ToString(CultureInfo.InvariantCulture)
                    + ": got \"" + answer + "\", want \"" + first + "\"");
                return 1;
            }
            i++;
        }
        GC.KeepAlive(checksum);

        using var output = new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false)) { AutoFlush = false };
        for (var j = 0; j < i; j++)
        {
            output.Write(ns[j].ToString(CultureInfo.InvariantCulture));
            output.Write('\n');
        }
        if (i > 0)
        {
            output.Write("answer ");
            output.Write(Encoding.UTF8.GetByteCount(first!).ToString(CultureInfo.InvariantCulture));
            output.Write('\n');
            output.Write(first);
            output.Write('\n');
        }
        output.Write("done\n");
        output.Flush();
        // Park until the runner has read our peak RSS; newline or EOF releases.
        Console.In.ReadLine();
        return 0;
    }

    private static ulong ElapsedNs(long t0, long freq)
    {
        var dt = Stopwatch.GetTimestamp() - t0;
        return (ulong)(dt / freq * 1_000_000_000L + (dt % freq) * 1_000_000_000L / freq);
    }

    // Not inlined so the JIT/AOT compiler cannot hoist or elide the call.
    [MethodImpl(MethodImplOptions.NoInlining)]
    private static string Call(Func<string, string> process, string input) => process(input);
}

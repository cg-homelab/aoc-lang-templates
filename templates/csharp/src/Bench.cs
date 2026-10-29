// In-process timing harness used by `just bench`. You never need to edit
// this file: it calls Part1.Process / Part2.Process and prints one JSON line
// (schema 1) for bench_day.py.
//
// Protocol: <cmd> bench <1|2> <input-path> <iterations>
using System.Diagnostics;
using System.Globalization;
using System.Runtime.CompilerServices;
using System.Text;

namespace Day__DAY__;

public static class Bench
{
    private const string Usage = "usage: __NAME__ bench <1|2> <input-path> <iterations>";

    public static int Run(string[] args)
    {
        if (args.Length != 3 || (args[0] != "1" && args[0] != "2")
            || !int.TryParse(args[2], NumberStyles.None, CultureInfo.InvariantCulture, out var iterations)
            || iterations < 1)
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

        // Allocate and touch the sample buffer up front so its pages are not
        // counted in the RSS delta.
        var ns = new long[iterations];
        Array.Fill(ns, -1L);
        var rssBefore = MaxRssBytes();
        var freq = Stopwatch.Frequency;
        string? first = null;
        long checksum = 0;
        for (var i = 0; i < iterations; i++)
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
                    + ": got \"" + Escape(answer) + "\", want \"" + Escape(first) + "\"");
                return 1;
            }
        }
        GC.KeepAlive(checksum);
        var rssAfter = MaxRssBytes();

        var sb = new StringBuilder(96 + 12 * iterations);
        sb.Append("{\"schema\":1,\"part\":").Append(part)
          .Append(",\"iterations\":").Append(iterations.ToString(CultureInfo.InvariantCulture))
          .Append(",\"ns\":[");
        for (var i = 0; i < ns.Length; i++)
        {
            if (i > 0) sb.Append(',');
            sb.Append(ns[i].ToString(CultureInfo.InvariantCulture));
        }
        sb.Append("],\"answer\":\"").Append(Escape(first!))
          .Append("\",\"max_rss_before\":").Append(Json(rssBefore))
          .Append(",\"max_rss_after\":").Append(Json(rssAfter))
          .Append('}');
        Console.Out.Write(sb.Append('\n').ToString());
        Console.Out.Flush();
        return 0;
    }

    // Not inlined so the JIT/AOT compiler cannot hoist or elide the call.
    [MethodImpl(MethodImplOptions.NoInlining)]
    private static string Call(Func<string, string> process, string input) => process(input);

    /// <summary>JSON string body escaping (without the surrounding quotes).</summary>
    public static string Escape(string s)
    {
        var sb = new StringBuilder(s.Length);
        foreach (var c in s)
        {
            switch (c)
            {
                case '"': sb.Append("\\\""); break;
                case '\\': sb.Append("\\\\"); break;
                case '\n': sb.Append("\\n"); break;
                case '\r': sb.Append("\\r"); break;
                case '\t': sb.Append("\\t"); break;
                default:
                    if (c < 0x20)
                        sb.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                    else
                        sb.Append(c);
                    break;
            }
        }
        return sb.ToString();
    }

    private static string Json(long? v) =>
        v.HasValue ? v.Value.ToString(CultureInfo.InvariantCulture) : "null";

    // Peak working set in bytes; null when the platform does not report it.
    private static long? MaxRssBytes()
    {
        try
        {
            using var self = Process.GetCurrentProcess();
            var peak = self.PeakWorkingSet64;
            return peak > 0 ? peak : null;
        }
        catch (Exception)
        {
            return null;
        }
    }
}

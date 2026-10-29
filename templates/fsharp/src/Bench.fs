module Day__DAY__.Bench

// In-process timing harness used by `just bench`. You never need to edit
// this file: it calls Part1.solve / Part2.solve and prints one JSON line
// (schema 1) for bench_day.py.
//
// Protocol: <cmd> bench <1|2> <input-path> <iterations>
//
// No printf/sprintf/failwithf here (not Native AOT friendly): StringBuilder,
// `string` and Console.Out/Console.Error only.

open System
open System.Diagnostics
open System.Globalization
open System.IO
open System.Runtime.CompilerServices
open System.Text

let private usage = "usage: __NAME__ bench <1|2> <input-path> <iterations>"

/// JSON string body escaping (without the surrounding quotes).
let escape (s: string) : string =
    let sb = StringBuilder(s.Length)
    for c in s do
        match c with
        | '"' -> sb.Append("\\\"") |> ignore
        | '\\' -> sb.Append("\\\\") |> ignore
        | '\n' -> sb.Append("\\n") |> ignore
        | '\r' -> sb.Append("\\r") |> ignore
        | '\t' -> sb.Append("\\t") |> ignore
        | c when c < ' ' -> sb.Append("\\u").Append((int c).ToString("x4", CultureInfo.InvariantCulture)) |> ignore
        | c -> sb.Append(c) |> ignore
    sb.ToString()

let private json (v: int64 option) : string =
    match v with
    | Some b -> b.ToString(CultureInfo.InvariantCulture)
    | None -> "null"

// Peak working set in bytes; None when the platform does not report it.
let private maxRssBytes () : int64 option =
    try
        use self = Process.GetCurrentProcess()
        let peak = self.PeakWorkingSet64
        if peak > 0L then Some peak else None
    with _ ->
        None

// Not inlined so neither the F# optimizer nor the AOT compiler can hoist or
// elide the call.
[<MethodImpl(MethodImplOptions.NoInlining)>]
let private call (solve: string -> string) (input: string) : string = solve input

let private parseIterations (s: string) : int option =
    match Int32.TryParse(s, NumberStyles.None, CultureInfo.InvariantCulture) with
    | true, n when n >= 1 -> Some n
    | _ -> None

let private usageError () : int =
    Console.Error.WriteLine usage
    1

let private measure (part: string) (path: string) (iterations: int) : int =
    let solve: string -> string = if part = "1" then Part1.solve else Part2.solve
    let input =
        try
            Some(File.ReadAllText path)
        with _ ->
            None
    match input with
    | None ->
        Console.Error.WriteLine("bench: cannot read " + path)
        1
    | Some input ->
        // Allocate and touch the sample buffer up front so its pages are not
        // counted in the RSS delta.
        let ns = Array.create iterations -1L
        let rssBefore = maxRssBytes ()
        let freq = Stopwatch.Frequency
        let mutable first: string = null
        let mutable checksum = 0L
        let mutable failed = false
        let mutable i = 0
        while not failed && i < iterations do
            let t0 = Stopwatch.GetTimestamp()
            let answer = call solve input
            let dt = Stopwatch.GetTimestamp() - t0
            ns.[i] <- dt / freq * 1_000_000_000L + (dt % freq) * 1_000_000_000L / freq
            checksum <- checksum + int64 answer.Length
            if isNull first then
                first <- answer
            elif not (String.Equals(answer, first, StringComparison.Ordinal)) then
                Console.Error.WriteLine(
                    "bench: answer mismatch at iteration " + string i
                    + ": got \"" + escape answer + "\", want \"" + escape first + "\"")
                failed <- true
            i <- i + 1
        GC.KeepAlive(box checksum)
        if failed then
            1
        else
            let rssAfter = maxRssBytes ()
            let sb = StringBuilder(96 + 12 * iterations)
            sb.Append("{\"schema\":1,\"part\":").Append(part)
              .Append(",\"iterations\":").Append(string iterations)
              .Append(",\"ns\":[").Append(String.Join(",", ns |> Array.map string))
              .Append("],\"answer\":\"").Append(escape first)
              .Append("\",\"max_rss_before\":").Append(json rssBefore)
              .Append(",\"max_rss_after\":").Append(json rssAfter)
              .Append("}\n") |> ignore
            Console.Out.Write(sb.ToString())
            Console.Out.Flush()
            0

let run (args: string[]) : int =
    match args with
    | [| ("1" | "2") as part; path; iterationsArg |] ->
        match parseIterations iterationsArg with
        | Some iterations -> measure part path iterations
        | None -> usageError ()
    | _ -> usageError ()

module Day__DAY__.Bench

// In-process timing harness used by `just bench`. You never need to edit
// this file: it calls Part1.solve / Part2.solve and prints one decimal
// nanosecond sample per line, then (only if at least one iteration ran)
// `answer <N>` (N = UTF-8 byte length of the answer) followed by the answer
// and a newline, then always a `done` line. After `done` it flushes and
// blocks on one stdin line (newline or EOF) so the runner can read this
// process's peak RSS while it is still alive, then exits 0.
//
// Protocol: <cmd> bench <1|2> <input-path> <budget-ns> <min-iter> <max-iter>
//
// Runs at least min-iter and at most max-iter iterations, continuing past
// min-iter only while the elapsed loop time is below budget-ns.
//
// No printf/sprintf/failwithf here (not Native AOT friendly): `string`,
// StreamWriter and Console.Error only.

open System
open System.Diagnostics
open System.Globalization
open System.IO
open System.Runtime.CompilerServices
open System.Text

let private usage =
    "usage: __NAME__ bench <1|2> <input-path> <budget-ns> <min-iter> <max-iter>"

// Not inlined so neither the F# optimizer nor the AOT compiler can hoist or
// elide the call.
[<MethodImpl(MethodImplOptions.NoInlining)>]
let private call (solve: string -> string) (input: string) : string = solve input

let private parseInt64 (s: string) : int64 option =
    match Int64.TryParse(s, NumberStyles.None, CultureInfo.InvariantCulture) with
    | true, n -> Some n
    | _ -> None

let private usageError () : int =
    Console.Error.WriteLine usage
    1

let private elapsedNs (t0: int64) : int64 =
    let dt = Stopwatch.GetTimestamp() - t0
    let freq = Stopwatch.Frequency
    dt / freq * 1_000_000_000L + (dt % freq) * 1_000_000_000L / freq

let private measure (part: string) (path: string) (budgetNs: int64) (minIter: int) (maxIter: int) : int =
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
        // Allocate and touch the sample buffer up front, identically whether
        // the loop runs zero or many times.
        let ns = Array.create maxIter -1L
        let mutable first: string = null
        let mutable checksum = 0L
        let mutable failed = false
        let mutable i = 0
        let tLoop = Stopwatch.GetTimestamp()
        while not failed && (i < minIter || (i < maxIter && elapsedNs tLoop < budgetNs)) do
            let t0 = Stopwatch.GetTimestamp()
            let answer = call solve input
            ns.[i] <- elapsedNs t0
            checksum <- checksum + int64 answer.Length
            if isNull first then
                first <- answer
            elif not (String.Equals(answer, first, StringComparison.Ordinal)) then
                Console.Error.WriteLine(
                    "bench: answer mismatch at iteration " + string i
                    + ": got \"" + answer + "\", want \"" + first + "\"")
                failed <- true
            i <- i + 1
        GC.KeepAlive(box checksum)
        if failed then
            1
        else
            use out = new StreamWriter(Console.OpenStandardOutput(), UTF8Encoding(false), 65536, AutoFlush = false)
            for k in 0 .. i - 1 do
                out.Write(ns.[k].ToString(CultureInfo.InvariantCulture))
                out.Write('\n')
            if i > 0 then
                out.Write("answer " + string (Encoding.UTF8.GetByteCount first) + "\n")
                out.Write(first)
                out.Write('\n')
            out.Write("done\n")
            out.Flush()
            Console.In.ReadLine() |> ignore
            0

let run (args: string[]) : int =
    match args with
    | [| ("1" | "2") as part; path; budgetArg; minArg; maxArg |] ->
        match parseInt64 budgetArg, parseInt64 minArg, parseInt64 maxArg with
        | Some budget, Some minIter, Some maxIter
            when minIter <= maxIter && maxIter <= int64 Int32.MaxValue ->
            measure part path budget (int minIter) (int maxIter)
        | _ -> usageError ()
    | _ -> usageError ()

module Day__DAY__.Program

open System
open System.IO

// No printf/printfn/sprintf here: FSharp.Core's printf machinery is
// reflection-based and not Native AOT friendly. Use `string` and
// Console.WriteLine instead.
[<EntryPoint>]
let main argv =
    match argv with
    | [| ("1" | "2") as part |] ->
        if not (File.Exists "input.txt") then
            Console.Error.WriteLine "input.txt missing — paste your puzzle input"
            1
        else
            let input = File.ReadAllText "input.txt"
            let answer = if part = "1" then Part1.solve input else Part2.solve input
            Console.WriteLine answer
            0
    | _ ->
        Console.Error.WriteLine "usage: __NAME__ <1|2>"
        1

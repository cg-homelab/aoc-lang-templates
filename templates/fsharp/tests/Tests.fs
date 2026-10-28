module Day__DAY__.Tests

open System
open System.IO
open Xunit

let private example name =
    File.ReadAllText(Path.Combine(AppContext.BaseDirectory, name))

[<Fact>]
let ``part1 example`` () =
    Assert.Equal("3", Part1.solve (example "example1.txt"))

[<Fact>]
let ``part2 example`` () =
    Assert.Equal("9", Part2.solve (example "example2.txt"))

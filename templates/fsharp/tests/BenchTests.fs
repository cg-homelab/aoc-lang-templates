module Day__DAY__.BenchTests

// Covers the bench harness's hand-rolled JSON string escaper (src/Bench.fs).
open Xunit

[<Fact>]
let ``escapes JSON strings`` () =
    Assert.Equal("plain 123", Bench.escape "plain 123")
    Assert.Equal("a\\\"b\\\\c", Bench.escape "a\"b\\c")
    Assert.Equal("x\\ny\\rz\\t", Bench.escape "x\ny\rz\t")
    Assert.Equal("\\u0001\\u001f", Bench.escape "\u0001\u001f")
    Assert.Equal("ünï", Bench.escape "ünï")

namespace Day__DAY__.Tests;

// Covers the bench harness's hand-rolled JSON string escaper (src/Bench.cs).
public class BenchTests
{
    [Fact]
    public void EscapesJsonStrings()
    {
        Assert.Equal("plain 123", Bench.Escape("plain 123"));
        Assert.Equal("a\\\"b\\\\c", Bench.Escape("a\"b\\c"));
        Assert.Equal("x\\ny\\rz\\t", Bench.Escape("x\ny\rz\t"));
        Assert.Equal("\\u0001\\u001f", Bench.Escape("\u0001\u001f"));
        Assert.Equal("ünï", Bench.Escape("ünï"));
    }
}

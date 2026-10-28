namespace Day__DAY__.Tests;

public class SolutionTests
{
    private static string Example(string name) =>
        File.ReadAllText(Path.Combine(AppContext.BaseDirectory, name));

    [Fact]
    public void Part1Example() =>
        Assert.Equal("3", Part1.Process(Example("example1.txt")));

    [Fact]
    public void Part2Example() =>
        Assert.Equal("9", Part2.Process(Example("example2.txt")));
}

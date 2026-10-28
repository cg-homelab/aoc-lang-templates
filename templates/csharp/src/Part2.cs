namespace Day__DAY__;

public static class Part2
{
    public static string Process(string input)
    {
        // Placeholder: total number of characters across all lines.
        var total = 0;
        foreach (var line in input.TrimEnd('\n').Split('\n'))
        {
            total += line.Length;
        }
        return total.ToString();
    }
}

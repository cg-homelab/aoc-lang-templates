using Day__DAY__;

if (args.Length != 1 || (args[0] != "1" && args[0] != "2"))
{
    Console.Error.WriteLine("usage: __NAME__ <1|2>");
    return 1;
}

if (!File.Exists("input.txt"))
{
    Console.Error.WriteLine("input.txt missing — paste your puzzle input");
    return 1;
}

var input = File.ReadAllText("input.txt");
Console.WriteLine(args[0] == "1" ? Part1.Process(input) : Part2.Process(input));
return 0;

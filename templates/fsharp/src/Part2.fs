module Day__DAY__.Part2

let solve (input: string) : string =
    // Placeholder: total number of characters across all lines.
    input.TrimEnd('\n').Split('\n') |> Array.sumBy String.length |> string

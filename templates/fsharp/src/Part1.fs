module Day__DAY__.Part1

// Named `solve` rather than `process`: `process` is reserved for future use
// in F# and triggers warning FS0046.
let solve (input: string) : string =
    // Placeholder: number of lines in the input.
    let lines = input.TrimEnd('\n').Split('\n')
    string lines.Length

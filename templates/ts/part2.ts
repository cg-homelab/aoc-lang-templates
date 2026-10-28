export function process(input: string): string {
  // Placeholder: total number of characters across all lines.
  const total = input
    .replace(/\n+$/, "")
    .split("\n")
    .reduce((sum, line) => sum + line.length, 0);
  return String(total);
}

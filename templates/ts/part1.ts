export function process(input: string): string {
  // Placeholder: number of lines in the input.
  return String(input.replace(/\n+$/, "").split("\n").length);
}

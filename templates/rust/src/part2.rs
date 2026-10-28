pub fn process(input: &str) -> String {
    // Placeholder: total number of characters across all lines.
    input.lines().map(str::len).sum::<usize>().to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn example() {
        let input = include_str!("../example2.txt");
        assert_eq!(process(input), "9");
    }
}

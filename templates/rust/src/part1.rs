pub fn process(input: &str) -> String {
    // Placeholder: number of lines in the input.
    input.lines().count().to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn example() {
        let input = include_str!("../example1.txt");
        assert_eq!(process(input), "3");
    }
}

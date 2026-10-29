mod bench;
mod part1;
mod part2;

use std::process::ExitCode;
use std::{env, fs};

fn main() -> ExitCode {
    let args: Vec<String> = env::args().collect();
    if args.get(1).map(String::as_str) == Some("bench") {
        return bench::run(&args[2..]);
    }
    let part = args.get(1).cloned().unwrap_or_default();
    if part != "1" && part != "2" {
        eprintln!("usage: __NAME__ <1|2>");
        return ExitCode::FAILURE;
    }
    let Ok(input) = fs::read_to_string("input.txt") else {
        eprintln!("input.txt missing — paste your puzzle input");
        return ExitCode::FAILURE;
    };
    match part.as_str() {
        "1" => println!("{}", part1::process(&input)),
        _ => println!("{}", part2::process(&input)),
    }
    ExitCode::SUCCESS
}

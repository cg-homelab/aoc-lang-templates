//! In-process timing harness used by `just bench`. You never need to edit
//! this file: it calls `part1::process` / `part2::process` and prints raw
//! samples for the bench runner.
//!
//! Protocol:
//! `<cmd> bench <1|2> <input-path> <budget-ns> <min-iter> <max-iter>`
//!
//! The loop runs at least `min-iter` times, then keeps going until `budget-ns`
//! of wall time has elapsed or `max-iter` samples are taken. Stdout is one
//! decimal nanosecond sample per line, then (only if at least one iteration
//! ran) a line `answer <N>`, exactly N bytes of answer (UTF-8, no trailing
//! newline) and a newline, then always a line `done`. After flushing, the
//! harness blocks until one line (or EOF) arrives on stdin, so the runner can
//! read its peak RSS while it is still alive, then exits 0.
//! Usage errors, unreadable input and answer mismatches print to stderr and
//! exit 1 with nothing on stdout and no wait.

use std::fs;
use std::hint::black_box;
use std::io::{BufWriter, Write};
use std::process::ExitCode;
use std::time::Instant;

use crate::{part1, part2};

const USAGE: &str = "usage: __NAME__ bench <1|2> <input-path> <budget-ns> <min-iter> <max-iter>";

/// Plain decimal digits only (`str::parse` would also accept a leading `+`).
fn digits(s: &str) -> bool {
    !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit())
}

pub fn run(args: &[String]) -> ExitCode {
    let [part, path, budget, min_iter, max_iter] = args else {
        eprintln!("{USAGE}");
        return ExitCode::FAILURE;
    };
    let process: fn(&str) -> String = match part.as_str() {
        "1" => part1::process,
        "2" => part2::process,
        _ => {
            eprintln!("{USAGE}");
            return ExitCode::FAILURE;
        }
    };
    if ![budget, min_iter, max_iter].into_iter().all(|s| digits(s)) {
        eprintln!("{USAGE}");
        return ExitCode::FAILURE;
    }
    let (Ok(budget_ns), Ok(min_iter), Ok(max_iter)) = (
        budget.parse::<u64>(),
        min_iter.parse::<u32>(),
        max_iter.parse::<u32>(),
    ) else {
        eprintln!("{USAGE}");
        return ExitCode::FAILURE;
    };
    if max_iter < min_iter {
        eprintln!("{USAGE}");
        return ExitCode::FAILURE;
    }
    let (min_iter, max_iter) = (min_iter as usize, max_iter as usize);
    let Ok(input) = fs::read_to_string(path) else {
        eprintln!("bench: cannot read {path}");
        return ExitCode::FAILURE;
    };

    // Allocate and touch the sample buffer up front, identically whether the
    // loop runs zero or many times.
    let mut ns = vec![u64::MAX; max_iter];
    let mut first: Option<String> = None;
    let mut checksum: usize = 0;
    let budget_ns = u128::from(budget_ns);
    let t_loop = Instant::now();
    let mut i = 0;
    while i < min_iter || (i < max_iter && t_loop.elapsed().as_nanos() < budget_ns) {
        let t0 = Instant::now();
        let answer = black_box(process(black_box(&input)));
        let elapsed = t0.elapsed().as_nanos() as u64;
        ns[i] = elapsed;
        checksum = checksum.wrapping_add(answer.len());
        match &first {
            None => first = Some(answer),
            Some(want) if *want != answer => {
                eprintln!(
                    "bench: answer mismatch at iteration {i}: got \"{answer}\", want \"{want}\""
                );
                return ExitCode::FAILURE;
            }
            Some(_) => {}
        }
        i += 1;
    }
    black_box(checksum);

    let mut out = BufWriter::new(std::io::stdout().lock());
    let mut res = Ok(());
    for t in &ns[..i] {
        res = res.and_then(|()| writeln!(out, "{t}"));
    }
    if let Some(answer) = &first {
        res = res.and_then(|()| writeln!(out, "answer {}\n{answer}", answer.len()));
    }
    res = res.and_then(|()| writeln!(out, "done"));
    if res.and_then(|()| out.flush()).is_err() {
        eprintln!("bench: cannot write to stdout");
        return ExitCode::FAILURE;
    }
    // Park until the runner has sampled our RSS; newline or EOF releases.
    let _ = std::io::stdin().read_line(&mut String::new());
    ExitCode::SUCCESS
}

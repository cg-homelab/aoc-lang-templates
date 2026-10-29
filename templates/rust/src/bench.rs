//! In-process timing harness used by `just bench`. You never need to edit
//! this file: it calls `part1::process` / `part2::process` and prints one
//! JSON line (schema 1) for `bench_day.py`.
//!
//! Protocol: `<cmd> bench <1|2> <input-path> <iterations>`

use std::fmt::Write as _;
use std::fs;
use std::hint::black_box;
use std::process::ExitCode;
use std::time::Instant;

use crate::{part1, part2};

const USAGE: &str = "usage: __NAME__ bench <1|2> <input-path> <iterations>";

pub fn run(args: &[String]) -> ExitCode {
    let [part, path, iterations] = args else {
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
    let iterations = match iterations.parse::<u32>() {
        Ok(n) if n > 0 => n as usize,
        _ => {
            eprintln!("{USAGE}");
            return ExitCode::FAILURE;
        }
    };
    let Ok(input) = fs::read_to_string(path) else {
        eprintln!("bench: cannot read {path}");
        return ExitCode::FAILURE;
    };

    // Allocate and touch the sample buffer up front so its pages are not
    // counted in the RSS delta.
    let mut ns = vec![u64::MAX; iterations];
    let rss_before = max_rss_bytes();
    let mut first: Option<String> = None;
    let mut checksum: usize = 0;
    for (i, slot) in ns.iter_mut().enumerate() {
        let t0 = Instant::now();
        let answer = black_box(process(black_box(&input)));
        let elapsed = t0.elapsed().as_nanos() as u64;
        *slot = elapsed;
        checksum = checksum.wrapping_add(answer.len());
        match &first {
            None => first = Some(answer),
            Some(want) if *want != answer => {
                eprintln!(
                    "bench: answer mismatch at iteration {i}: got \"{}\", want \"{}\"",
                    escape(&answer),
                    escape(want)
                );
                return ExitCode::FAILURE;
            }
            Some(_) => {}
        }
    }
    black_box(checksum);
    let rss_after = max_rss_bytes();

    let mut line = String::with_capacity(64 + 12 * ns.len());
    let _ = write!(
        line,
        "{{\"schema\":1,\"part\":{part},\"iterations\":{iterations},\"ns\":["
    );
    for (i, t) in ns.iter().enumerate() {
        if i > 0 {
            line.push(',');
        }
        let _ = write!(line, "{t}");
    }
    let _ = write!(
        line,
        "],\"answer\":\"{}\",\"max_rss_before\":{},\"max_rss_after\":{}}}",
        escape(first.as_deref().unwrap_or("")),
        json_opt(rss_before),
        json_opt(rss_after)
    );
    println!("{line}");
    ExitCode::SUCCESS
}

/// JSON string body escaping (without the surrounding quotes).
fn escape(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => {
                let _ = write!(out, "\\u{:04x}", c as u32);
            }
            c => out.push(c),
        }
    }
    out
}

fn json_opt(v: Option<u64>) -> String {
    v.map_or_else(|| "null".to_string(), |b| b.to_string())
}

#[cfg(all(unix, target_pointer_width = "64"))]
fn max_rss_bytes() -> Option<u64> {
    use std::os::raw::c_int;

    // Hand-declared `struct rusage` (LP64 layout: two timevals, then 14
    // longs) so the template needs no `libc` crate.
    #[repr(C)]
    struct Rusage {
        ru_utime: [i64; 2],
        ru_stime: [i64; 2],
        ru_maxrss: i64,
        _rest: [i64; 13],
    }
    extern "C" {
        fn getrusage(who: c_int, usage: *mut Rusage) -> c_int;
    }
    const RUSAGE_SELF: c_int = 0;

    let mut usage = Rusage {
        ru_utime: [0; 2],
        ru_stime: [0; 2],
        ru_maxrss: 0,
        _rest: [0; 13],
    };
    // SAFETY: `usage` is a valid, writable struct with the C layout above.
    if unsafe { getrusage(RUSAGE_SELF, &mut usage) } != 0 || usage.ru_maxrss < 0 {
        return None;
    }
    let maxrss = usage.ru_maxrss as u64;
    // Linux reports kilobytes, macOS bytes.
    Some(if cfg!(target_os = "macos") {
        maxrss
    } else {
        maxrss * 1024
    })
}

#[cfg(not(all(unix, target_pointer_width = "64")))]
fn max_rss_bytes() -> Option<u64> {
    None
}

#[cfg(test)]
mod tests {
    use super::escape;

    #[test]
    fn escape_json_string() {
        assert_eq!(escape("plain 123"), "plain 123");
        assert_eq!(escape("a\"b\\c"), "a\\\"b\\\\c");
        assert_eq!(escape("x\ny\rz\t"), "x\\ny\\rz\\t");
        assert_eq!(escape("\u{1}\u{1f}"), "\\u0001\\u001f");
        assert_eq!(escape("ünï"), "ünï");
    }
}

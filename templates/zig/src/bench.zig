//! In-process timing harness used by `just bench`. You never need to edit
//! this file: it calls part1.process / part2.process repeatedly and prints
//! the per-call timings for `aoc-toolkit bench`.
//!
//! Protocol: <cmd> bench <1|2> <input-path> <budget-ns> <min-iter> <max-iter>
//!
//! Runs at least min-iter and at most max-iter iterations, stopping early
//! once budget-ns has elapsed since the loop started. stdout gets one line
//! per iteration (nanoseconds, decimal); then, only if at least one
//! iteration ran, `answer <N>` followed by exactly N bytes of answer and a
//! newline; then always a line `done`. After `done` the harness waits for
//! one line on stdin (newline or EOF) before exiting 0, so the runner can
//! read this process's peak RSS while it is still alive. Usage errors,
//! unreadable input and answer mismatches go to stderr with exit code 1,
//! nothing on stdout and no wait.
const std = @import("std");
const part1 = @import("part1.zig");
const part2 = @import("part2.zig");

const usage = "usage: __NAME__ bench <1|2> <input-path> <budget-ns> <min-iter> <max-iter>\n";

pub fn run(init: std.process.Init, args: []const []const u8) !u8 {
    const arena = init.arena.allocator();
    const io = init.io;

    if (args.len != 5) {
        std.debug.print(usage, .{});
        return 1;
    }
    const part = args[0];
    const path = args[1];
    const process = if (std.mem.eql(u8, part, "1"))
        &part1.process
    else if (std.mem.eql(u8, part, "2"))
        &part2.process
    else {
        std.debug.print(usage, .{});
        return 1;
    };
    const budget_ns = parseUnsigned(u64, args[2]) orelse {
        std.debug.print(usage, .{});
        return 1;
    };
    const min_iter = parseUnsigned(u32, args[3]) orelse {
        std.debug.print(usage, .{});
        return 1;
    };
    const max_iter = parseUnsigned(u32, args[4]) orelse {
        std.debug.print(usage, .{});
        return 1;
    };
    if (max_iter < min_iter) {
        std.debug.print(usage, .{});
        return 1;
    }

    const input = std.Io.Dir.cwd().readFileAlloc(io, path, arena, .unlimited) catch {
        std.debug.print("bench: cannot read {s}\n", .{path});
        return 1;
    };

    // `process` allocates from its own arena, which is reset (keeping its
    // pages) between iterations, outside the timed window.
    var scratch: std.heap.ArenaAllocator = .init(std.heap.page_allocator);
    defer scratch.deinit();
    const scratch_allocator = scratch.allocator();
    // Allocate and touch the sample buffer up front, sized max-iter, so the
    // process looks the same whether the loop runs zero times (the runner's
    // idle baseline) or many times.
    const ns = try arena.alloc(u64, max_iter);
    @memset(ns, std.math.maxInt(u64));
    var first: []const u8 = "";
    var checksum: usize = 0;

    const t_loop = std.Io.Timestamp.now(io, .awake);
    var i: u32 = 0;
    while (i < min_iter or (i < max_iter and elapsedNs(io, t_loop) < budget_ns)) : (i += 1) {
        const t0 = std.Io.Timestamp.now(io, .awake);
        const answer = try process(scratch_allocator, input);
        const t1 = std.Io.Timestamp.now(io, .awake);
        std.mem.doNotOptimizeAway(answer.ptr);
        ns[i] = @intCast(t0.durationTo(t1).toNanoseconds());
        checksum +%= answer.len;
        if (i == 0) {
            first = try arena.dupe(u8, answer);
        } else if (!std.mem.eql(u8, answer, first)) {
            std.debug.print("bench: answer mismatch at iteration {d}: got {s}, want {s}\n", .{ i, answer, first });
            return 1;
        }
        _ = scratch.reset(.retain_capacity);
    }
    std.mem.doNotOptimizeAway(checksum);

    var buffer: [4096]u8 = undefined;
    var stdout = std.Io.File.stdout().writerStreaming(io, &buffer);
    const w = &stdout.interface;
    for (ns[0..i]) |t| try w.print("{d}\n", .{t});
    if (i > 0) try w.print("answer {d}\n{s}\n", .{ first.len, first });
    try w.writeAll("done\n");
    try w.flush();

    // Park until the runner sends a line or closes stdin; content and read
    // errors are ignored.
    var in_buffer: [64]u8 = undefined;
    var stdin = std.Io.File.stdin().readerStreaming(io, &in_buffer);
    while (stdin.interface.takeByte()) |c| {
        if (c == '\n') break;
    } else |_| {}
    return 0;
}

/// Nanoseconds elapsed on the monotonic clock since `start`.
fn elapsedNs(io: std.Io, start: std.Io.Timestamp) u64 {
    const d = start.durationTo(std.Io.Timestamp.now(io, .awake)).toNanoseconds();
    return if (d <= 0) 0 else @intCast(d);
}

/// Plain decimal digits only (no sign, no underscores), or null.
fn parseUnsigned(comptime T: type, s: []const u8) ?T {
    if (s.len == 0) return null;
    for (s) |c| if (!std.ascii.isDigit(c)) return null;
    return std.fmt.parseInt(T, s, 10) catch null;
}

test "parseUnsigned" {
    try std.testing.expectEqual(@as(?u32, 0), parseUnsigned(u32, "0"));
    try std.testing.expectEqual(@as(?u32, 42), parseUnsigned(u32, "42"));
    try std.testing.expectEqual(@as(?u32, null), parseUnsigned(u32, ""));
    try std.testing.expectEqual(@as(?u32, null), parseUnsigned(u32, "-1"));
    try std.testing.expectEqual(@as(?u32, null), parseUnsigned(u32, "+1"));
    try std.testing.expectEqual(@as(?u32, null), parseUnsigned(u32, "1_0"));
    try std.testing.expectEqual(@as(?u32, null), parseUnsigned(u32, "4294967296"));
}

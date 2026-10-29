//! In-process timing harness used by `just bench`. You never need to edit
//! this file: it calls part1.process / part2.process and prints one JSON
//! line (schema 1) for bench_day.py.
//!
//! Protocol: <cmd> bench <1|2> <input-path> <iterations>
const std = @import("std");
const builtin = @import("builtin");
const part1 = @import("part1.zig");
const part2 = @import("part2.zig");

const usage = "usage: __NAME__ bench <1|2> <input-path> <iterations>\n";

pub fn run(init: std.process.Init, args: []const []const u8) !u8 {
    const arena = init.arena.allocator();
    const io = init.io;

    if (args.len != 3) {
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
    const iterations = std.fmt.parseInt(u32, args[2], 10) catch 0;
    if (iterations == 0) {
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
    // Allocate and touch the sample buffer up front so its pages are not
    // counted in the RSS delta.
    const ns = try arena.alloc(u64, iterations);
    @memset(ns, std.math.maxInt(u64));
    var first: []const u8 = "";
    var checksum: usize = 0;

    const rss_before = maxRssBytes();
    for (ns, 0..) |*slot, i| {
        // VERIFY in CI: Io.Timestamp.now(io, .awake) / durationTo /
        // toNanoseconds (confirmed in the 0.17.0 std source, lib/std/Io.zig).
        const t0 = std.Io.Timestamp.now(io, .awake);
        const answer = try process(scratch_allocator, input);
        const t1 = std.Io.Timestamp.now(io, .awake);
        std.mem.doNotOptimizeAway(answer.ptr);
        slot.* = @intCast(t0.durationTo(t1).toNanoseconds());
        checksum +%= answer.len;
        if (i == 0) {
            first = try arena.dupe(u8, answer);
        } else if (!std.mem.eql(u8, answer, first)) {
            var err_buffer: [512]u8 = undefined;
            var stderr = std.Io.File.stderr().writerStreaming(io, &err_buffer);
            const ew = &stderr.interface;
            try ew.print("bench: answer mismatch at iteration {d}: got \"", .{i});
            try writeEscaped(ew, answer);
            try ew.writeAll("\", want \"");
            try writeEscaped(ew, first);
            try ew.writeAll("\"\n");
            try ew.flush();
            return 1;
        }
        _ = scratch.reset(.retain_capacity);
    }
    std.mem.doNotOptimizeAway(checksum);
    const rss_after = maxRssBytes();

    var buffer: [4096]u8 = undefined;
    var stdout = std.Io.File.stdout().writerStreaming(io, &buffer);
    const w = &stdout.interface;
    try w.print("{{\"schema\":1,\"part\":{s},\"iterations\":{d},\"ns\":[", .{ part, iterations });
    for (ns, 0..) |t, i| {
        if (i > 0) try w.writeByte(',');
        try w.print("{d}", .{t});
    }
    try w.writeAll("],\"answer\":\"");
    try writeEscaped(w, first);
    try w.writeAll("\",\"max_rss_before\":");
    try writeOptional(w, rss_before);
    try w.writeAll(",\"max_rss_after\":");
    try writeOptional(w, rss_after);
    try w.writeAll("}\n");
    try w.flush();
    return 0;
}

/// JSON string body escaping (without the surrounding quotes). Bytes >= 0x80
/// pass through unchanged, so UTF-8 stays UTF-8.
fn writeEscaped(w: *std.Io.Writer, s: []const u8) std.Io.Writer.Error!void {
    const hex = "0123456789abcdef";
    for (s) |c| switch (c) {
        '"' => try w.writeAll("\\\""),
        '\\' => try w.writeAll("\\\\"),
        '\n' => try w.writeAll("\\n"),
        '\r' => try w.writeAll("\\r"),
        '\t' => try w.writeAll("\\t"),
        0...0x08, 0x0b, 0x0c, 0x0e...0x1f => {
            try w.writeAll("\\u00");
            try w.writeByte(hex[c >> 4]);
            try w.writeByte(hex[c & 0xf]);
        },
        else => try w.writeByte(c),
    };
}

fn writeOptional(w: *std.Io.Writer, v: ?u64) std.Io.Writer.Error!void {
    if (v) |bytes| try w.print("{d}", .{bytes}) else try w.writeAll("null");
}

/// Peak resident set size in bytes, or null where getrusage is unavailable.
fn maxRssBytes() ?u64 {
    // VERIFY in CI: std.posix.getrusage(std.posix.rusage.SELF).maxrss
    // (confirmed in the 0.17.0 std source, lib/std/posix.zig).
    if (comptime builtin.os.tag == .windows or !@hasDecl(std.posix.system, "getrusage")) {
        return null;
    } else {
        const ru = std.posix.getrusage(std.posix.rusage.SELF);
        if (ru.maxrss < 0) return null;
        const maxrss: u64 = @intCast(ru.maxrss);
        // Linux reports kilobytes, macOS bytes.
        return if (comptime builtin.os.tag.isDarwin()) maxrss else maxrss * 1024;
    }
}

test "writeEscaped" {
    var buf: [64]u8 = undefined;
    var w: std.Io.Writer = .fixed(&buf);
    try writeEscaped(&w, "a\"b\\c\nd\re\tf\x01\x1fü");
    try std.testing.expectEqualStrings("a\\\"b\\\\c\\nd\\re\\tf\\u0001\\u001fü", w.buffered());
}

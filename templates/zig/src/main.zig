const std = @import("std");
const part1 = @import("part1.zig");
const part2 = @import("part2.zig");

pub fn main(init: std.process.Init) !u8 {
    const arena = init.arena.allocator();
    const io = init.io;

    const args = try init.minimal.args.toSlice(arena);
    const part = if (args.len == 2) args[1] else "";
    const process = if (std.mem.eql(u8, part, "1"))
        &part1.process
    else if (std.mem.eql(u8, part, "2"))
        &part2.process
    else {
        std.debug.print("usage: day-__DAY__ <1|2>\n", .{});
        return 1;
    };

    const input = std.Io.Dir.cwd().readFileAlloc(io, "input.txt", arena, .unlimited) catch |err| switch (err) {
        error.FileNotFound => {
            std.debug.print("input.txt missing — paste your puzzle input\n", .{});
            return 1;
        },
        else => |e| return e,
    };

    const answer = try process(arena, input);

    var buffer: [256]u8 = undefined;
    var stdout = std.Io.File.stdout().writerStreaming(io, &buffer);
    try stdout.interface.print("{s}\n", .{answer});
    try stdout.interface.flush();
    return 0;
}

// The tests live next to the code in part1.zig/part2.zig. Referencing the
// imports from a test block is what makes `zig build test` analyze them.
test {
    _ = part1;
    _ = part2;
}

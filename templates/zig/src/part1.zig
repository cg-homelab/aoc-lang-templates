const std = @import("std");

pub fn process(gpa: std.mem.Allocator, input: []const u8) std.mem.Allocator.Error![]u8 {
    // Placeholder: number of lines in the input.
    var count: usize = 0;
    var lines = std.mem.splitScalar(u8, std.mem.trimEnd(u8, input, "\n"), '\n');
    while (lines.next()) |_| count += 1;
    return gpa.print("{d}", .{count});
}

test "example" {
    const answer = try process(std.testing.allocator, @embedFile("example1"));
    defer std.testing.allocator.free(answer);
    try std.testing.expectEqualStrings("3", answer);
}

const std = @import("std");

pub fn process(gpa: std.mem.Allocator, input: []const u8) std.mem.Allocator.Error![]u8 {
    // Placeholder: total number of characters across all lines.
    var total: usize = 0;
    var lines = std.mem.splitScalar(u8, std.mem.trimEnd(u8, input, "\n"), '\n');
    while (lines.next()) |line| total += line.len;
    return gpa.print("{d}", .{total});
}

test "example" {
    const answer = try process(std.testing.allocator, @embedFile("example2"));
    defer std.testing.allocator.free(answer);
    try std.testing.expectEqualStrings("9", answer);
}

const std = @import("std");

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{});
    const optimize = b.standardOptimizeOption(.{});

    // One module holds main.zig, part1.zig and part2.zig. It is the root
    // module of both the executable and the test binary.
    const mod = b.createModule(.{
        .root_source_file = b.path("src/main.zig"),
        .target = target,
        .optimize = optimize,
    });

    // The example inputs live at the day root, outside this module's src/
    // root, so `@embedFile("../example1.txt")` is rejected ("embed of file
    // outside package path"). Exposing them as anonymous imports lets the
    // tests write `@embedFile("example1")` / `@embedFile("example2")`.
    mod.addAnonymousImport("example1", .{ .root_source_file = b.path("example1.txt") });
    mod.addAnonymousImport("example2", .{ .root_source_file = b.path("example2.txt") });

    const exe = b.addExecutable(.{
        .name = "day-__DAY__",
        .root_module = mod,
    });
    b.installArtifact(exe);

    // `zig build run -- 1` for convenience; the justfile runs the installed
    // binary directly so run and bench use the exact same artifact.
    const run_cmd = b.addRunArtifact(exe);
    run_cmd.step.dependOn(b.getInstallStep());
    run_cmd.addPassthruArgs();
    const run_step = b.step("run", "Run the solution: zig build run -- <1|2>");
    run_step.dependOn(&run_cmd.step);

    // `zig build test` runs every `test` block reachable from src/main.zig.
    const tests = b.addTest(.{ .root_module = mod });
    const run_tests = b.addRunArtifact(tests);
    const test_step = b.step("test", "Run the tests against example1.txt/example2.txt");
    test_step.dependOn(&run_tests.step);
}

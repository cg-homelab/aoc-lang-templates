import sys
from pathlib import Path

import bench
from part1 import process as part1
from part2 import process as part2


def main() -> None:
    if sys.argv[1:2] == ["bench"]:
        sys.exit(bench.main(sys.argv[2:]))

    part = sys.argv[1] if len(sys.argv) > 1 else ""
    if part not in ("1", "2"):
        sys.exit("usage: python main.py <1|2>")

    input_path = Path(__file__).parent / "input.txt"
    if not input_path.is_file():
        sys.exit("input.txt missing — paste your puzzle input")
    text = input_path.read_text()

    print(part1(text) if part == "1" else part2(text))


if __name__ == "__main__":
    main()

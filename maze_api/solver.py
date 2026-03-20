#!/usr/bin/env python3
"""Pure Python solver for ASCII mazes."""

from __future__ import annotations

import argparse
import sys
import time
from collections import deque
from dataclasses import dataclass

from .server import Cell, DIRECTIONS, MazeGame, positive_int


@dataclass(frozen=True)
class ParsedMaze:
    rows: int
    cols: int
    passages: dict[Cell, set[str]]
    start_cell: Cell
    goal_cell: Cell
    exit_direction: str | None
    lines: list[str]


@dataclass(frozen=True)
class SolveResult:
    moves: list[str]
    elapsed_seconds: float
    path: list[Cell]
    solved_ascii: str

    @property
    def move_string(self) -> str:
        return "".join(self.moves)


def parse_ascii_maze(ascii_maze: str) -> ParsedMaze:
    lines = _normalize_lines(ascii_maze)
    if len(lines) < 3 or len(lines) % 2 == 0:
        raise ValueError("ASCII maze must have an odd number of lines and at least one cell.")

    width = len(lines[0])
    if (width - 1) % 4 != 0:
        raise ValueError("ASCII maze width is not compatible with the expected grid format.")

    if any(len(line) != width for line in lines):
        raise ValueError("ASCII maze lines must have a consistent width.")

    rows = (len(lines) - 1) // 2
    cols = (width - 1) // 4
    passages = {Cell(row, col): set() for row in range(rows) for col in range(cols)}

    player_cell: Cell | None = None
    start_cell: Cell | None = None
    goal_marker_cell: Cell | None = None
    boundary_openings: list[tuple[Cell, str]] = []

    for row in range(rows):
        north_line = lines[2 * row]
        interior_line = lines[2 * row + 1]
        south_line = lines[2 * row + 2]

        for col in range(cols):
            cell = Cell(row, col)
            center_index = 4 * col + 2
            marker = interior_line[center_index]
            if marker == "P":
                player_cell = cell
            elif marker == "S":
                start_cell = cell
            elif marker == "X":
                goal_marker_cell = cell

            north_open = north_line[4 * col + 1 : 4 * col + 4] == "   "
            south_open = south_line[4 * col + 1 : 4 * col + 4] == "   "
            west_open = interior_line[4 * col] == " "
            east_open = interior_line[4 * col + 4] == " "

            if north_open:
                if row == 0:
                    boundary_openings.append((cell, "N"))
                else:
                    passages[cell].add("N")

            if south_open:
                if row == rows - 1:
                    boundary_openings.append((cell, "S"))
                else:
                    passages[cell].add("S")

            if west_open:
                if col == 0:
                    boundary_openings.append((cell, "W"))
                else:
                    passages[cell].add("W")

            if east_open:
                if col == cols - 1:
                    boundary_openings.append((cell, "E"))
                else:
                    passages[cell].add("E")

    start = player_cell or start_cell
    if start is None:
        raise ValueError('ASCII maze must mark the starting cell with "P" or "S".')

    goal_cell, exit_direction = _resolve_goal(goal_marker_cell, boundary_openings)
    return ParsedMaze(
        rows=rows,
        cols=cols,
        passages=passages,
        start_cell=start,
        goal_cell=goal_cell,
        exit_direction=exit_direction,
        lines=lines,
    )


def solve_ascii_maze(ascii_maze: str) -> SolveResult:
    start_time = time.perf_counter()
    parsed = parse_ascii_maze(ascii_maze)
    path, moves = _bfs_shortest_path(parsed)
    solved_ascii = _render_solution(parsed, path)
    elapsed_seconds = time.perf_counter() - start_time
    return SolveResult(
        moves=moves,
        elapsed_seconds=elapsed_seconds,
        path=path,
        solved_ascii=solved_ascii,
    )


def _normalize_lines(ascii_maze: str) -> list[str]:
    raw_lines = ascii_maze.splitlines()
    while raw_lines and raw_lines[-1] == "":
        raw_lines.pop()
    if not raw_lines:
        raise ValueError("ASCII maze is empty.")

    width = max(len(line) for line in raw_lines)
    return [line.ljust(width) for line in raw_lines]


def _resolve_goal(
    goal_marker_cell: Cell | None,
    boundary_openings: list[tuple[Cell, str]],
) -> tuple[Cell, str | None]:
    if goal_marker_cell is not None:
        matching_directions = [direction for cell, direction in boundary_openings if cell == goal_marker_cell]
        if len(matching_directions) > 1:
            raise ValueError("Goal cell cannot expose more than one exit to the outside.")
        exit_direction = matching_directions[0] if matching_directions else None
        return goal_marker_cell, exit_direction

    if len(boundary_openings) != 1:
        raise ValueError('ASCII maze must have exactly one boundary opening when "X" is absent.')

    return boundary_openings[0]


def _bfs_shortest_path(parsed: ParsedMaze) -> tuple[list[Cell], list[str]]:
    queue = deque([parsed.start_cell])
    parents: dict[Cell, tuple[Cell | None, str | None]] = {
        parsed.start_cell: (None, None)
    }

    while queue:
        current = queue.popleft()
        if current == parsed.goal_cell:
            break

        for direction in DIRECTIONS:
            if direction not in parsed.passages[current]:
                continue

            neighbor = _neighbor(current, direction)
            if neighbor in parents:
                continue

            parents[neighbor] = (current, direction)
            queue.append(neighbor)

    if parsed.goal_cell not in parents:
        raise ValueError("Maze has no path from the start to the goal.")

    path: list[Cell] = []
    moves: list[str] = []
    current = parsed.goal_cell
    while current is not None:
        path.append(current)
        parent, move = parents[current]
        if move is not None:
            moves.append(move)
        current = parent

    path.reverse()
    moves.reverse()
    if parsed.exit_direction is not None:
        moves.append(parsed.exit_direction)
    return path, moves


def _neighbor(cell: Cell, direction: str) -> Cell:
    delta_row, delta_col = DIRECTIONS[direction]
    return Cell(cell.row + delta_row, cell.col + delta_col)


def _render_solution(parsed: ParsedMaze, path: list[Cell]) -> str:
    canvas = [list(line) for line in parsed.lines]

    for cell in path[1:-1]:
        row_index = 2 * cell.row + 1
        col_index = 4 * cell.col + 2
        if canvas[row_index][col_index] == " ":
            canvas[row_index][col_index] = "."

    goal_row = 2 * parsed.goal_cell.row + 1
    goal_col = 4 * parsed.goal_cell.col + 2
    if canvas[goal_row][goal_col] == " ":
        canvas[goal_row][goal_col] = "X"

    return "\n".join("".join(row) for row in canvas)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Solve an ASCII maze in pure Python and print the moves.",
    )
    parser.add_argument(
        "--file",
        help="Path to a text file containing the ASCII maze.",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read the ASCII maze from standard input.",
    )
    parser.add_argument(
        "--size",
        type=positive_int,
        help="Generate and solve a maze of size n x n.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional seed used with --size to reproduce the same generated maze.",
    )
    return parser.parse_args()


def load_maze_from_args(args: argparse.Namespace) -> tuple[str, str]:
    chosen_sources = sum(
        source is not None
        for source in (
            args.file,
            args.size,
        )
    ) + int(args.stdin)
    if chosen_sources != 1:
        raise ValueError("Choose exactly one maze source: --file, --stdin or --size.")

    if args.file is not None:
        with open(args.file, "r", encoding="utf-8") as handle:
            return handle.read(), f"file={args.file}"

    if args.stdin:
        return sys.stdin.read(), "stdin"

    game = MazeGame(size=args.size, seed=args.seed)
    return game.ascii_maze(), f"generated size={args.size} seed={game.current_seed}"


def main() -> None:
    args = parse_args()
    ascii_maze, source_description = load_maze_from_args(args)
    result = solve_ascii_maze(ascii_maze)

    print(f"Source: {source_description}")
    print(f"Moves: {result.move_string}")
    print(f"Move count: {len(result.moves)}")
    print(f"Elapsed seconds: {result.elapsed_seconds:.9f}")
    print("Solved maze:")
    print(result.solved_ascii)


if __name__ == "__main__":
    main()

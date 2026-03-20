import tempfile
import unittest

from maze_api import MazeGame, parse_ascii_maze, solve_ascii_maze
from maze_api.solver import load_maze_from_args
from maze_api.server import Cell


class MazeSolverTest(unittest.TestCase):
    def test_solver_finds_valid_moves_for_generated_maze(self) -> None:
        game = MazeGame(size=6, seed=17)
        ascii_maze = game.ascii_maze()

        result = solve_ascii_maze(ascii_maze)

        self.assertTrue(result.moves)
        self.assertGreaterEqual(result.elapsed_seconds, 0.0)
        self.assertIn("X", result.solved_ascii)
        self.assertTrue(self._moves_escape_maze(game, result.moves))

    def test_parser_accepts_boundary_exit_without_x(self) -> None:
        ascii_maze = "\n".join(
            [
                "+---+---+",
                "| P     |",
                "+---+   +",
                "|       |",
                "+---+   +",
            ]
        )

        parsed = parse_ascii_maze(ascii_maze)

        self.assertEqual(parsed.start_cell, Cell(0, 0))
        self.assertEqual(parsed.goal_cell, Cell(1, 1))
        self.assertEqual(parsed.exit_direction, "S")

    def test_load_maze_from_generated_args_reports_seed(self) -> None:
        class Args:
            file = None
            stdin = False
            size = 4
            seed = 222

        ascii_maze, description = load_maze_from_args(Args())
        self.assertIn("seed=222", description)
        self.assertIn("P", ascii_maze)

    def test_load_maze_from_file(self) -> None:
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8") as handle:
            handle.write("+---+\n| P |\n+   +\n")
            handle.flush()

            class Args:
                file = handle.name
                stdin = False
                size = None
                seed = None

            ascii_maze, description = load_maze_from_args(Args())

        self.assertEqual(description, f"file={handle.name}")
        self.assertIn("P", ascii_maze)

    def _moves_escape_maze(self, game: MazeGame, moves: list[str]) -> bool:
        position = game.position
        for index, move in enumerate(moves):
            visible = game.maze.visible_directions(position)
            seen = visible[move]
            if seen == "wall":
                return False

            is_last = index == len(moves) - 1
            if seen == "exit":
                return is_last

            if is_last:
                return False

            next_position = game.maze.neighbor(position, move)
            if next_position is None:
                return False
            position = next_position

        return False


if __name__ == "__main__":
    unittest.main()

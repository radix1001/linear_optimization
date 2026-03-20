import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from maze_api import Cell, MazeGame, create_server


class MazeGameTest(unittest.TestCase):
    def test_exit_is_unique(self) -> None:
        game = MazeGame(size=6, seed=11)
        exit_count = 0
        for row in range(game.maze.size):
            for col in range(game.maze.size):
                visible = game.maze.visible_directions(Cell(row, col))
                exit_count += sum(1 for item in visible.values() if item == "exit")
        self.assertEqual(exit_count, 1)

    def test_same_seed_recreates_same_maze(self) -> None:
        first = MazeGame(size=5, seed=12345)
        second = MazeGame(size=5, seed=12345)

        self.assertEqual(first.state()["seed"], second.state()["seed"])
        self.assertEqual(first.state()["start"], second.state()["start"])
        self.assertEqual(first.maze.exit_cell, second.maze.exit_cell)
        self.assertEqual(first.maze.exit_direction, second.maze.exit_direction)
        self.assertEqual(first.ascii_maze(), second.ascii_maze())

    def test_state_has_four_directions(self) -> None:
        game = MazeGame(size=4, seed=9)
        state = game.state()
        self.assertEqual(set(state["directions"]), {"N", "S", "E", "W"})
        self.assertTrue(state["allowed_moves"])
        self.assertIn("seed", state)


class MazeAPITest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server(size=5, port=0, seed=21)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_ascii_endpoint_returns_maze(self) -> None:
        ascii_maze = self._get_text("/ascii")
        self.assertIn("+", ascii_maze)
        self.assertIn("P", ascii_maze)

    def test_reset_with_seed_is_reproducible(self) -> None:
        first = self._post_json("/reset", {"seed": 2026})
        first_ascii = self._get_text("/ascii")
        second = self._post_json("/reset", {"seed": 2026})
        second_ascii = self._get_text("/ascii")
        self.assertEqual(first["seed"], 2026)
        self.assertEqual(first["start"], second["start"])
        self.assertEqual(first_ascii, second_ascii)

    def test_state_and_move_flow(self) -> None:
        state = self._get_json("/state")
        self.assertEqual(state["status"], "playing")
        self.assertEqual(set(state["directions"]), {"N", "S", "E", "W"})

        direction = state["allowed_moves"][0]
        moved = self._get_json(f"/move?direction={direction}")
        self.assertEqual(moved["moves"], 1)
        self.assertEqual(moved["seed"], state["seed"])

    def test_invalid_direction_returns_400(self) -> None:
        with self.assertRaises(HTTPError) as ctx:
            self._get_json("/move?direction=X")
        self.assertEqual(ctx.exception.code, 400)

    def _get_json(self, path: str) -> dict:
        with urlopen(f"{self.base_url}{path}") as response:
            return json.loads(response.read().decode("utf-8"))

    def _get_text(self, path: str) -> str:
        with urlopen(f"{self.base_url}{path}") as response:
            return response.read().decode("utf-8")

    def _post_json(self, path: str, payload: dict) -> dict:
        request = Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()

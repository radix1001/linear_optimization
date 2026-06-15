import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from maze_api import Cell, MazeGame, ResultStore, create_server
from maze_api.client import MazeClient, solve_by_download, solve_by_exploration


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
        self.server.maze_result_store.close()

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

    def test_health_endpoint(self) -> None:
        self.assertEqual(self._get_json("/health"), {"status": "ok"})

    def test_init_creates_independent_session(self) -> None:
        created = self._post_json("/init", {"size": 1000, "solver": "tester"})
        self.assertEqual(created["solver"], "tester")
        self.assertEqual(created["size"], 1000)
        self.assertEqual(created["grid"], 32)
        self.assertEqual(created["cells"], 32 * 32)
        self.assertIn("session_id", created)
        self.assertNotEqual(created["session_id"], "default")
        self.assertEqual(created["status"], "playing")
        self.assertGreaterEqual(created["elapsed_seconds"], 0.0)

    def test_init_rejects_disallowed_size(self) -> None:
        with self.assertRaises(HTTPError) as ctx:
            self._post_json("/init", {"size": 5})
        self.assertEqual(ctx.exception.code, 400)

    def test_init_rejects_seed(self) -> None:
        with self.assertRaises(HTTPError) as ctx:
            self._post_json("/init", {"size": 1000, "seed": 7})
        self.assertEqual(ctx.exception.code, 400)

    def test_unknown_session_returns_404(self) -> None:
        with self.assertRaises(HTTPError) as ctx:
            self._get_json("/state?session=does-not-exist")
        self.assertEqual(ctx.exception.code, 404)

    def test_full_run_is_timed_and_recorded(self) -> None:
        client = MazeClient(self.base_url, solver="dfs-explorer")
        initial = client.init(size=1000)
        final = solve_by_exploration(client, initial)

        self.assertEqual(final["status"], "won")
        self.assertGreater(final["moves"], 0)
        self.assertGreaterEqual(final["elapsed_seconds"], 0.0)
        self.assertIn("finished_at", final)
        self.assertIn("escaped in", final["message"])

        board = client.leaderboard(size=1000)
        self.assertEqual(board["count"], 1)
        entry = board["leaderboard"][0]
        self.assertEqual(entry["solver"], "dfs-explorer")
        self.assertEqual(entry["size"], 1000)
        self.assertEqual(entry["grid"], 32)
        self.assertEqual(entry["moves"], final["moves"])
        self.assertEqual(entry["rank"], 1)

    def test_batch_download_solver_handles_million_cells(self) -> None:
        client = MazeClient(self.base_url, solver="batch-bfs")
        initial = client.init(size=1_000_000)
        self.assertEqual(initial["grid"], 1000)
        self.assertEqual(initial["cells"], 1_000_000)

        final = solve_by_download(client, initial)
        self.assertEqual(final["status"], "won")
        self.assertGreater(final["moves"], 0)
        self.assertEqual(final["moves_applied"], final["moves"])

        board = client.leaderboard(size=1_000_000)
        self.assertEqual(board["leaderboard"][0]["grid"], 1000)

    def test_moves_rejects_bad_payload(self) -> None:
        created = self._post_json("/init", {"size": 1000})
        with self.assertRaises(HTTPError) as ctx:
            self._post_json("/moves", {"session": created["session_id"], "moves": 123})
        self.assertEqual(ctx.exception.code, 400)

    def test_moves_reports_wall_with_progress(self) -> None:
        state = self._get_json("/state")
        wall_direction = next(d for d, kind in state["directions"].items() if kind == "wall")
        with self.assertRaises(HTTPError) as ctx:
            self._post_json("/moves", {"moves": wall_direction})
        self.assertEqual(ctx.exception.code, 409)
        payload = json.loads(ctx.exception.read().decode("utf-8"))
        self.assertEqual(payload["moves_applied"], 0)

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


class ResultStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = ResultStore(":memory:")

    def tearDown(self) -> None:
        self.store.close()

    def _record(self, solver: str, size: int, moves: int, elapsed: float) -> None:
        self.store.record(
            {
                "session_id": solver,
                "solver": solver,
                "size": size,
                "seed": 1,
                "moves": moves,
                "elapsed_seconds": elapsed,
                "started_at": 1000.0,
                "finished_at": 1000.0 + elapsed,
            }
        )

    def test_leaderboard_orders_by_time_then_moves(self) -> None:
        self._record("slow", 10000, moves=50, elapsed=3.0)
        self._record("fast", 10000, moves=80, elapsed=1.0)
        self._record("medium", 10000, moves=60, elapsed=2.0)

        by_time = self.store.leaderboard(size=10000, order="time")
        self.assertEqual([e["solver"] for e in by_time], ["fast", "medium", "slow"])
        self.assertEqual([e["rank"] for e in by_time], [1, 2, 3])
        self.assertEqual(by_time[0]["grid"], 100)

        by_moves = self.store.leaderboard(size=10000, order="moves")
        self.assertEqual([e["solver"] for e in by_moves], ["slow", "medium", "fast"])

    def test_leaderboard_filters_by_size(self) -> None:
        self._record("a", 1000, moves=10, elapsed=1.0)
        self._record("b", 100000, moves=10, elapsed=1.0)
        self.assertEqual(self.store.leaderboard(size=1000)[0]["solver"], "a")
        self.assertEqual(len(self.store.leaderboard(size=1000)), 1)


if __name__ == "__main__":
    unittest.main()

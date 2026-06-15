#!/usr/bin/env python3
"""Tiny client for the maze API plus an example exploration solver.

The course writes solvers; each solver only needs to:

1. ``init()`` to start a timed run and get a ``session_id``.
2. read ``state["directions"]`` (what is N/S/E/W: ``wall``/``path``/``exit``)
   and the absolute ``state["position"]``,
3. ``move()`` until ``state["status"] == "won"``.

Only the standard library is used, so this runs anywhere Python does.

Example::

    python -m maze_api.client --url https://your-app.onrender.com --size 1000 --solver dfs
"""

from __future__ import annotations

import argparse
import json
from urllib.request import Request, urlopen

DIRECTIONS = {"N": (-1, 0), "S": (1, 0), "E": (0, 1), "W": (0, -1)}
OPPOSITE = {"N": "S", "S": "N", "E": "W", "W": "E"}


class MazeClient:
    """Thin HTTP wrapper around a running maze API."""

    def __init__(self, base_url: str, solver: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.solver = solver
        self.session_id: str | None = None

    def init(self, size: int) -> dict:
        """Start a timed run. ``size`` is a cell count: 1000, 10000 or 100000."""
        body: dict = {"size": size}
        if self.solver is not None:
            body["solver"] = self.solver
        state = self._post("/init", body)
        self.session_id = state["session_id"]
        return state

    def state(self) -> dict:
        return self._get(f"/state?session={self.session_id}")

    def move(self, direction: str) -> dict:
        return self._post("/move", {"session": self.session_id, "direction": direction})

    def submit_path(self, moves) -> dict:
        """Send a whole path in one request. ``moves`` is a string or a list."""
        if not isinstance(moves, str):
            moves = "".join(moves)
        return self._post("/moves", {"session": self.session_id, "moves": moves})

    def ascii(self) -> str:
        with urlopen(f"{self.base_url}/ascii?session={self.session_id}") as response:
            return response.read().decode("utf-8")

    def leaderboard(self, size: int | None = None, order: str = "time", limit: int = 20) -> dict:
        query = f"/leaderboard?order={order}&limit={limit}"
        if size is not None:
            query += f"&size={size}"
        return self._get(query)

    def _get(self, path: str) -> dict:
        with urlopen(f"{self.base_url}{path}") as response:
            return json.loads(response.read().decode("utf-8"))

    def _post(self, path: str, payload: dict) -> dict:
        request = Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))


def _neighbor(position: dict, direction: str) -> tuple[int, int]:
    d_row, d_col = DIRECTIONS[direction]
    return (position["row"] + d_row, position["col"] + d_col)


def solve_by_exploration(client: MazeClient, state: dict) -> dict:
    """Depth-first exploration that only uses what is visible from each cell.

    This is a deliberately simple reference solver: it always finds the exit
    but does not take the shortest path. Course solvers can do better.
    """
    visited: set[tuple[int, int]] = set()
    backtrack: list[str] = []

    while state["status"] == "playing":
        position = state["position"]
        visited.add((position["row"], position["col"]))
        directions = state["directions"]

        exit_direction = next((d for d, kind in directions.items() if kind == "exit"), None)
        if exit_direction is not None:
            state = client.move(exit_direction)
            continue

        next_direction = None
        for direction in ("N", "S", "E", "W"):
            if directions.get(direction) == "path" and _neighbor(position, direction) not in visited:
                next_direction = direction
                break

        if next_direction is not None:
            backtrack.append(OPPOSITE[next_direction])
            state = client.move(next_direction)
        elif backtrack:
            state = client.move(backtrack.pop())
        else:
            raise RuntimeError("No path to the exit was found.")

    return state


def solve_by_download(client: MazeClient, state: dict) -> dict:
    """Download the whole maze, solve it locally, and submit the path at once.

    This is the practical approach for large mazes: it makes a single /moves
    request instead of one round trip per step, so the timing reflects the
    solver's compute rather than network latency.
    """
    from .solver import solve_ascii_maze

    result = solve_ascii_maze(client.ascii())
    return client.submit_path(result.move_string)


SOLVERS = {"explore": solve_by_exploration, "download": solve_by_download}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the example maze solver against a maze API.")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="Base URL of the maze API.")
    parser.add_argument(
        "--size",
        type=int,
        choices=(1000, 10000, 100000, 1000000),
        default=1000,
        help="Maze size to request from /init (number of cells).",
    )
    parser.add_argument(
        "--mode",
        choices=tuple(SOLVERS),
        default="download",
        help="explore: walk step by step. download: fetch the maze, solve locally, submit the path.",
    )
    parser.add_argument("--solver", default=None, help="Name recorded in the leaderboard.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    client = MazeClient(args.url, solver=args.solver or f"{args.mode}-demo")
    initial = client.init(size=args.size)
    print(
        f"Session {client.session_id} started "
        f"(size={initial['size']}, grid={initial['grid']}x{initial['grid']})."
    )

    final = SOLVERS[args.mode](client, initial)
    print(f"Status: {final['status']}")
    print(f"Moves: {final['moves']}")
    print(f"Elapsed seconds (server-side): {final['elapsed_seconds']:.6f}")


if __name__ == "__main__":
    main()

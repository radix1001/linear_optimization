#!/usr/bin/env python3
"""Local maze game exposed through a small HTTP API."""

from __future__ import annotations

import argparse
import json
import random
import threading
from collections import deque
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse


DIRECTIONS: dict[str, tuple[int, int]] = {
    "N": (-1, 0),
    "S": (1, 0),
    "E": (0, 1),
    "W": (0, -1),
}
OPPOSITE = {"N": "S", "S": "N", "E": "W", "W": "E"}
RANDOM_SEED_MAX = 2**63 - 1


def generate_seed() -> int:
    return random.SystemRandom().randrange(RANDOM_SEED_MAX)


@dataclass(frozen=True)
class Cell:
    row: int
    col: int


class Maze:
    """Perfect maze with a single exit to the outside."""

    def __init__(self, size: int, seed: int) -> None:
        if size < 1:
            raise ValueError("Maze size must be at least 1.")
        self.size = size
        self.seed = seed
        self._rng = random.Random(seed)
        self.passages: dict[Cell, set[str]] = {
            Cell(row, col): set()
            for row in range(size)
            for col in range(size)
        }
        self._generate()
        self.exit_cell, self.exit_direction = self._choose_exit()
        self.start_cell = self._farthest_cell_from(self.exit_cell)

    def _generate(self) -> None:
        start = Cell(0, 0)
        visited = {start}
        stack = [start]

        while stack:
            current = stack[-1]
            neighbors = self._unvisited_neighbors(current, visited)
            if not neighbors:
                stack.pop()
                continue

            direction, neighbor = self._rng.choice(neighbors)
            self.passages[current].add(direction)
            self.passages[neighbor].add(OPPOSITE[direction])
            visited.add(neighbor)
            stack.append(neighbor)

    def _unvisited_neighbors(
        self,
        cell: Cell,
        visited: set[Cell],
    ) -> list[tuple[str, Cell]]:
        neighbors: list[tuple[str, Cell]] = []
        for direction, (d_row, d_col) in DIRECTIONS.items():
            next_row = cell.row + d_row
            next_col = cell.col + d_col
            if 0 <= next_row < self.size and 0 <= next_col < self.size:
                neighbor = Cell(next_row, next_col)
                if neighbor not in visited:
                    neighbors.append((direction, neighbor))
        return neighbors

    def _boundary_options(self) -> list[tuple[Cell, str]]:
        options: list[tuple[Cell, str]] = []
        for row in range(self.size):
            for col in range(self.size):
                cell = Cell(row, col)
                if row == 0:
                    options.append((cell, "N"))
                if row == self.size - 1:
                    options.append((cell, "S"))
                if col == 0:
                    options.append((cell, "W"))
                if col == self.size - 1:
                    options.append((cell, "E"))
        return options

    def _choose_exit(self) -> tuple[Cell, str]:
        return self._rng.choice(self._boundary_options())

    def _farthest_cell_from(self, origin: Cell) -> Cell:
        visited = {origin}
        queue = deque([(origin, 0)])
        farthest = origin
        farthest_distance = 0

        while queue:
            current, distance = queue.popleft()
            if distance > farthest_distance:
                farthest = current
                farthest_distance = distance
            for direction in DIRECTIONS:
                if direction not in self.passages[current]:
                    continue
                neighbor = self.neighbor(current, direction)
                if neighbor is not None and neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, distance + 1))
        return farthest

    def neighbor(self, cell: Cell, direction: str) -> Cell | None:
        d_row, d_col = DIRECTIONS[direction]
        next_row = cell.row + d_row
        next_col = cell.col + d_col
        if 0 <= next_row < self.size and 0 <= next_col < self.size:
            return Cell(next_row, next_col)
        return None

    def visible_directions(self, cell: Cell) -> dict[str, str]:
        visible: dict[str, str] = {}
        for direction in DIRECTIONS:
            if cell == self.exit_cell and direction == self.exit_direction:
                visible[direction] = "exit"
            elif direction in self.passages[cell]:
                visible[direction] = "path"
            else:
                visible[direction] = "wall"
        return visible

    def ascii_representation(self, player: Cell | None = None) -> str:
        lines: list[str] = []
        top_border = "+"
        for col in range(self.size):
            cell = Cell(0, col)
            segment = "   " if cell == self.exit_cell and self.exit_direction == "N" else "---"
            top_border += f"{segment}+"
        lines.append(top_border)

        for row in range(self.size):
            interior = []
            west_wall = " "
            if not (Cell(row, 0) == self.exit_cell and self.exit_direction == "W"):
                west_wall = "|"
            interior.append(west_wall)

            bottom = ["+"]
            for col in range(self.size):
                cell = Cell(row, col)
                marker = self._cell_marker(cell, player)
                east_wall = " "
                if not (cell == self.exit_cell and self.exit_direction == "E") and "E" not in self.passages[cell]:
                    east_wall = "|"
                interior.append(f" {marker} {east_wall}")

                south_wall = "   "
                if not (cell == self.exit_cell and self.exit_direction == "S") and "S" not in self.passages[cell]:
                    south_wall = "---"
                bottom.append(f"{south_wall}+")

            lines.append("".join(interior))
            lines.append("".join(bottom))

        return "\n".join(lines)

    def _cell_marker(self, cell: Cell, player: Cell | None) -> str:
        if player == cell:
            return "P"
        if cell == self.start_cell:
            return "S"
        if cell == self.exit_cell:
            return "X"
        return " "


class MazeGame:
    def __init__(self, size: int, seed: int | None = None) -> None:
        self.size = size
        self._lock = threading.Lock()
        self.current_seed = seed if seed is not None else generate_seed()
        self._new_maze(self.current_seed)

    def _new_maze(self, seed: int) -> None:
        self.current_seed = seed
        self.maze = Maze(self.size, seed=seed)
        self.position = self.maze.start_cell
        self.moves = 0
        self.finished = False

    def reset(self, seed: int | None = None) -> dict[str, Any]:
        with self._lock:
            next_seed = seed if seed is not None else generate_seed()
            self._new_maze(next_seed)
            return self._state_payload("Game reset.")

    def state(self) -> dict[str, Any]:
        with self._lock:
            return self._state_payload()

    def ascii_maze(self) -> str:
        with self._lock:
            return self.maze.ascii_representation(player=self.position)

    def move(self, direction: str) -> tuple[int, dict[str, Any]]:
        normalized = direction.upper()
        if normalized not in DIRECTIONS:
            return (
                HTTPStatus.BAD_REQUEST,
                {"error": "Direction must be one of N, S, E, W."},
            )

        with self._lock:
            if self.finished:
                return (
                    HTTPStatus.CONFLICT,
                    {"error": "The game already finished. Call /reset to play again."},
                )

            visible = self.maze.visible_directions(self.position)
            seen = visible[normalized]
            if seen == "wall":
                return (
                    HTTPStatus.CONFLICT,
                    {
                        "error": f"Cannot move {normalized}; there is a wall there.",
                        "state": self._state_payload(),
                    },
                )

            self.moves += 1
            if seen == "exit":
                self.finished = True
                return (
                    HTTPStatus.OK,
                    self._state_payload("You escaped the maze."),
                )

            next_position = self.maze.neighbor(self.position, normalized)
            if next_position is None:
                return (
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "Maze state is inconsistent."},
                )

            self.position = next_position
            return (
                HTTPStatus.OK,
                self._state_payload(f"Moved {normalized}."),
            )

    def _state_payload(self, message: str | None = None) -> dict[str, Any]:
        directions = {} if self.finished else self.maze.visible_directions(self.position)
        return {
            "size": self.size,
            "seed": self.current_seed,
            "status": "won" if self.finished else "playing",
            "position": {"row": self.position.row, "col": self.position.col},
            "start": {"row": self.maze.start_cell.row, "col": self.maze.start_cell.col},
            "moves": self.moves,
            "directions": directions,
            "allowed_moves": [key for key, value in directions.items() if value != "wall"],
            "message": message,
        }


class MazeAPIHandler(BaseHTTPRequestHandler):
    game: MazeGame

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._json_response(
                HTTPStatus.OK,
                {
                    "message": "Maze API is running.",
                    "size": self.game.size,
                    "seed": self.game.current_seed,
                    "endpoints": {
                        "GET /state": "Returns the current position, seed and what is in N/S/E/W.",
                        "GET /move?direction=N": "Moves the player using N, S, E or W.",
                        "POST /move": 'Same move endpoint, with JSON body {"direction": "N"}.',
                        "POST /reset": 'Creates a new maze. Optional JSON body: {"seed": 123}.',
                        "GET /ascii": "Prints the current maze in ASCII.",
                    },
                },
            )
            return

        if parsed.path == "/state":
            self._json_response(HTTPStatus.OK, self.game.state())
            return

        if parsed.path == "/move":
            params = parse_qs(parsed.query)
            direction = params.get("direction", [None])[0]
            if direction is None:
                self._json_response(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "Query string must include direction."},
                )
                return
            status, payload = self.game.move(direction)
            self._json_response(status, payload)
            return

        if parsed.path == "/ascii":
            self._text_response(HTTPStatus.OK, self.game.ascii_maze())
            return

        self._json_response(HTTPStatus.NOT_FOUND, {"error": "Endpoint not found."})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/move":
            body = self._read_json_body()
            direction = body.get("direction") if isinstance(body, dict) else None
            if direction is None:
                self._json_response(
                    HTTPStatus.BAD_REQUEST,
                    {"error": 'JSON body must include "direction".'},
                )
                return
            status, payload = self.game.move(direction)
            self._json_response(status, payload)
            return

        if parsed.path == "/reset":
            body = self._read_json_body()
            seed = body.get("seed") if isinstance(body, dict) else None
            if seed is not None and not isinstance(seed, int):
                self._json_response(
                    HTTPStatus.BAD_REQUEST,
                    {"error": 'If provided, "seed" must be an integer.'},
                )
                return
            self._json_response(HTTPStatus.OK, self.game.reset(seed=seed))
            return

        self._json_response(HTTPStatus.NOT_FOUND, {"error": "Endpoint not found."})

    def _read_json_body(self) -> Any:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length == 0:
            return {}
        raw_body = self.rfile.read(content_length)
        try:
            return json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def _json_response(self, status: int, payload: dict[str, Any]) -> None:
        response = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def _text_response(self, status: int, payload: str) -> None:
        response = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format: str, *args: Any) -> None:
        return


def build_handler(game: MazeGame) -> type[MazeAPIHandler]:
    class BoundMazeAPIHandler(MazeAPIHandler):
        pass

    BoundMazeAPIHandler.game = game
    return BoundMazeAPIHandler


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("Value must be at least 1.")
    return parsed


def create_server(
    size: int,
    host: str = "127.0.0.1",
    port: int = 8000,
    seed: int | None = None,
) -> ThreadingHTTPServer:
    game = MazeGame(size=size, seed=seed)
    handler = build_handler(game)
    return ThreadingHTTPServer((host, port), handler)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a local maze game that is controlled through HTTP requests.",
    )
    parser.add_argument("n", type=positive_int, help="Size of the maze (n x n).")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host interface to bind the API to. Defaults to 127.0.0.1.",
    )
    parser.add_argument(
        "--port",
        type=positive_int,
        default=8000,
        help="Port to expose the API on. Defaults to 8000.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional seed for a reproducible maze.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    server = create_server(args.n, host=args.host, port=args.port, seed=args.seed)
    game = server.RequestHandlerClass.game
    print(f"Maze API listening on http://{args.host}:{args.port}")
    print(f"Size: {args.n}x{args.n}")
    print(f"Seed: {game.current_seed}")
    print("Use GET /state, GET or POST /move, and GET /ascii to play.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

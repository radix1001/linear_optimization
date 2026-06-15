#!/usr/bin/env python3
"""Maze game exposed through a small HTTP API.

The API is built only on the Python standard library so it can be deployed on
Render (or any other host) without installing dependencies.  A run works like
this:

1. A client (a "solver" written by the course) calls ``POST /init`` and gets a
   ``session_id``.  This starts a server-side timer for that session.
2. The client repeatedly sends commands with ``POST /move`` (or
   ``GET /move``), persisting its position on the server between requests.
3. When the player steps through the exit, the server records how long the
   run took and how many moves it needed, and stores the result so different
   solvers can be compared through ``GET /leaderboard``.
"""

from __future__ import annotations

import argparse
import array
import html
import json
import os
import random
import sqlite3
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
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
# Bit flags used to store open passages compactly (one byte per cell).
DIR_BITS = {"N": 1, "S": 2, "E": 4, "W": 8}
RANDOM_SEED_MAX = 2**63 - 1
DEFAULT_SESSION_ID = "default"

# The sizes the /init endpoint accepts, expressed as the target number of cells
# (10^3 ... 10^6) and mapped to the side length of a square grid.  (A side
# length of 10^4 would already be 10^8 cells, which is why the accepted value is
# a cell count, not a side length.)
ALLOWED_SIZES: dict[int, int] = {
    1_000: 32,        # 32 x 32     = 1024 cells
    10_000: 100,      # 100 x 100   = 10000 cells
    100_000: 316,     # 316 x 316   = 99856 cells
    1_000_000: 1000,  # 1000 x 1000 = 1000000 cells
}

# Inline stylesheet for the human-facing home and leaderboard pages.
_HTML_STYLE = (
    "<style>"
    "body{margin:0;background:#0f1220;color:#e6e6f0;"
    "font:16px/1.6 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}"
    ".wrap{max-width:880px;margin:0 auto;padding:32px 20px 64px}"
    "h1{font-size:28px;margin:0 0 6px}"
    "h2{font-size:20px;margin:30px 0 10px;border-bottom:1px solid #2a2e45;padding-bottom:6px}"
    "a{color:#8ab4ff;text-decoration:none}a:hover{text-decoration:underline}"
    "code{background:#1c2036;padding:2px 6px;border-radius:5px;font-size:14px;color:#c8d2ff}"
    "pre{background:#1c2036;padding:14px 16px;border-radius:8px;overflow:auto;font-size:13px}"
    "pre code{background:none;padding:0}"
    "table{border-collapse:collapse;width:100%;margin:8px 0}"
    "th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #2a2e45;font-size:14px}"
    "th{color:#9aa3c7;font-weight:600}"
    "ol li,ul li{margin:6px 0}"
    ".chip{display:inline-block;background:#1c2036;border:1px solid #2a2e45;border-radius:999px;"
    "padding:4px 12px;margin:3px 6px 3px 0;font-size:14px}"
    ".chip.sel{background:#2d5bff;border-color:#2d5bff;color:#fff}"
    ".muted{color:#9aa3c7}"
    ".empty{text-align:center;color:#9aa3c7;padding:18px}"
    "</style>"
)


def generate_seed() -> int:
    return random.SystemRandom().randrange(RANDOM_SEED_MAX)


def _now_iso(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat()


@dataclass(frozen=True)
class Cell:
    row: int
    col: int


class Maze:
    """Perfect maze with a single exit to the outside.

    Open passages are stored as one byte per cell (a bitmask of ``DIR_BITS``)
    in a ``bytearray`` indexed by ``row * size + col``.  This keeps even a
    1000x1000 maze (10^6 cells) to ~1 MB and a few seconds to build, instead
    of the gigabytes a dict of ``Cell`` objects would need.
    """

    def __init__(self, size: int, seed: int) -> None:
        if size < 1:
            raise ValueError("Maze size must be at least 1.")
        self.size = size
        self.seed = seed
        self._rng = random.Random(seed)
        self._passages = self._generate()
        self.exit_cell, self.exit_direction = self._choose_exit()
        self.start_cell = self._farthest_cell_from(self.exit_cell)

    def _index(self, cell: Cell) -> int:
        return cell.row * self.size + cell.col

    def _generate(self) -> bytearray:
        n = self.size
        passages = bytearray(n * n)
        visited = bytearray(n * n)
        rng = self._rng
        stack = array.array("i", [0])
        visited[0] = 1

        while stack:
            current = stack[-1]
            row, col = divmod(current, n)
            neighbors: list[tuple[int, int, int]] = []
            if row > 0 and not visited[current - n]:
                neighbors.append((1, current - n, 2))   # N, opposite S
            if row < n - 1 and not visited[current + n]:
                neighbors.append((2, current + n, 1))   # S, opposite N
            if col < n - 1 and not visited[current + 1]:
                neighbors.append((4, current + 1, 8))   # E, opposite W
            if col > 0 and not visited[current - 1]:
                neighbors.append((8, current - 1, 4))   # W, opposite E

            if not neighbors:
                stack.pop()
                continue

            bit, neighbor, opposite = rng.choice(neighbors)
            passages[current] |= bit
            passages[neighbor] |= opposite
            visited[neighbor] = 1
            stack.append(neighbor)

        return passages

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
        # BFS discovers cells in non-decreasing distance order, so the last
        # cell dequeued is one of the farthest from the origin.
        n = self.size
        passages = self._passages
        visited = bytearray(n * n)
        start = self._index(origin)
        visited[start] = 1
        queue = deque([start])
        farthest = start

        while queue:
            current = queue.popleft()
            farthest = current
            flags = passages[current]
            if flags & 1 and not visited[current - n]:
                visited[current - n] = 1
                queue.append(current - n)
            if flags & 2 and not visited[current + n]:
                visited[current + n] = 1
                queue.append(current + n)
            if flags & 4 and not visited[current + 1]:
                visited[current + 1] = 1
                queue.append(current + 1)
            if flags & 8 and not visited[current - 1]:
                visited[current - 1] = 1
                queue.append(current - 1)

        row, col = divmod(farthest, n)
        return Cell(row, col)

    def neighbor(self, cell: Cell, direction: str) -> Cell | None:
        d_row, d_col = DIRECTIONS[direction]
        next_row = cell.row + d_row
        next_col = cell.col + d_col
        if 0 <= next_row < self.size and 0 <= next_col < self.size:
            return Cell(next_row, next_col)
        return None

    def move_kind(self, cell: Cell, direction: str) -> str:
        """Return ``"exit"``, ``"path"`` or ``"wall"`` for one step. O(1)."""
        if cell == self.exit_cell and direction == self.exit_direction:
            return "exit"
        if self._passages[self._index(cell)] & DIR_BITS[direction]:
            return "path"
        return "wall"

    def visible_directions(self, cell: Cell) -> dict[str, str]:
        return {direction: self.move_kind(cell, direction) for direction in DIRECTIONS}

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
                flags = self._passages[row * self.size + col]
                marker = self._cell_marker(cell, player)
                east_wall = " "
                if not (cell == self.exit_cell and self.exit_direction == "E") and not (flags & DIR_BITS["E"]):
                    east_wall = "|"
                interior.append(f" {marker} {east_wall}")

                south_wall = "   "
                if not (cell == self.exit_cell and self.exit_direction == "S") and not (flags & DIR_BITS["S"]):
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
    """A single maze with a player position that persists across moves."""

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
                    {"error": "The game already finished. Start a new session with /init."},
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

    def apply_moves(self, moves: list[str]) -> tuple[int, dict[str, Any]]:
        """Apply a whole sequence of moves in a single locked pass.

        This is what makes large mazes practical: a solver submits its entire
        path in one request instead of one HTTP round trip per step.
        """
        with self._lock:
            if self.finished:
                return (
                    HTTPStatus.CONFLICT,
                    {"error": "The game already finished. Start a new session with /init."},
                )

            applied = 0
            for direction in moves:
                normalized = direction.upper()
                if normalized not in DIRECTIONS:
                    payload = self._state_payload()
                    payload["error"] = f"Invalid direction {direction!r} at move {applied}."
                    payload["moves_applied"] = applied
                    return (HTTPStatus.BAD_REQUEST, payload)

                kind = self.maze.move_kind(self.position, normalized)
                if kind == "wall":
                    payload = self._state_payload()
                    payload["error"] = f"Hit a wall moving {normalized} at move {applied}."
                    payload["moves_applied"] = applied
                    return (HTTPStatus.CONFLICT, payload)

                self.moves += 1
                applied += 1
                if kind == "exit":
                    self.finished = True
                    payload = self._state_payload(f"Applied {applied} moves and escaped.")
                    payload["moves_applied"] = applied
                    return (HTTPStatus.OK, payload)

                self.position = self.maze.neighbor(self.position, normalized)

            payload = self._state_payload(f"Applied {applied} moves.")
            payload["moves_applied"] = applied
            return (HTTPStatus.OK, payload)

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


class ResultStore:
    """Stores completed runs in SQLite so solvers can be compared.

    Pass an on-disk path (``MAZE_DB_PATH``) to keep results across restarts;
    the default ``:memory:`` database lives only for the lifetime of the
    process, which is enough for a single course session.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                solver TEXT,
                size INTEGER NOT NULL,
                seed INTEGER NOT NULL,
                moves INTEGER NOT NULL,
                elapsed_seconds REAL NOT NULL,
                started_at REAL NOT NULL,
                finished_at REAL NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        self._conn.commit()

    def record(self, result: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO results (
                    session_id, solver, size, seed, moves,
                    elapsed_seconds, started_at, finished_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result["session_id"],
                    result.get("solver"),
                    result["size"],
                    result["seed"],
                    result["moves"],
                    result["elapsed_seconds"],
                    result["started_at"],
                    result["finished_at"],
                    result["finished_at"],
                ),
            )
            self._conn.commit()

    def leaderboard(
        self,
        size: int | None = None,
        seed: int | None = None,
        order: str = "time",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        order_column = "moves" if order == "moves" else "elapsed_seconds"
        query = "SELECT * FROM results"
        clauses: list[str] = []
        params: list[Any] = []
        if size is not None:
            clauses.append("size = ?")
            params.append(size)
        if seed is not None:
            clauses.append("seed = ?")
            params.append(seed)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += f" ORDER BY {order_column} ASC, moves ASC, elapsed_seconds ASC LIMIT ?"
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(query, params).fetchall()

        leaderboard: list[dict[str, Any]] = []
        for rank, row in enumerate(rows, start=1):
            entry = dict(row)
            entry["rank"] = rank
            grid = ALLOWED_SIZES.get(entry["size"])
            if grid is not None:
                entry["grid"] = grid
                entry["cells"] = grid * grid
            entry["started_at_iso"] = _now_iso(entry["started_at"])
            entry["finished_at_iso"] = _now_iso(entry["finished_at"])
            leaderboard.append(entry)
        return leaderboard

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class Session:
    """A single maze run with its own timer and final result."""

    def __init__(
        self,
        session_id: str,
        size: int,
        seed: int | None,
        solver: str | None,
        result_store: ResultStore | None,
        clock=time.perf_counter,
        level: int | None = None,
    ) -> None:
        self.id = session_id
        self.solver = solver
        self.game = MazeGame(size=size, seed=seed)
        # The value reported as "size": the requested cell-count level for /init
        # sessions, or the grid side length for the manual default session.
        self.level = level if level is not None else size
        self._result_store = result_store
        self._clock = clock
        self.started_monotonic = clock()
        self.started_at = time.time()
        self.finished_monotonic: float | None = None
        self.finished_at: float | None = None
        self._recorded = False
        self._lock = threading.Lock()

    @property
    def elapsed_seconds(self) -> float:
        end = self.finished_monotonic if self.finished_monotonic is not None else self._clock()
        return end - self.started_monotonic

    def state(self) -> dict[str, Any]:
        return self._augment(self.game.state())

    def ascii_maze(self) -> str:
        return self.game.ascii_maze()

    def move(self, direction: str) -> tuple[int, dict[str, Any]]:
        status, payload = self.game.move(direction)
        return self._after_move(status, payload)

    def move_batch(self, moves: list[str]) -> tuple[int, dict[str, Any]]:
        status, payload = self.game.apply_moves(moves)
        return self._after_move(status, payload)

    def _after_move(self, status: int, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        just_won = False
        with self._lock:
            if self.game.finished and not self._recorded:
                self.finished_monotonic = self._clock()
                self.finished_at = time.time()
                self._recorded = True
                self._record_result()
                just_won = True
        enriched = self._augment(payload)
        if just_won:
            enriched["message"] = (
                f"You escaped in {enriched['elapsed_seconds']:.3f} s "
                f"after {self.game.moves} moves."
            )
        return status, enriched

    def _augment(self, payload: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(payload)
        enriched["session_id"] = self.id
        enriched["solver"] = self.solver
        enriched["size"] = self.level
        enriched["grid"] = self.game.size
        enriched["cells"] = self.game.size ** 2
        enriched["elapsed_seconds"] = round(self.elapsed_seconds, 9)
        enriched["started_at"] = self.started_at
        if self.finished_at is not None:
            enriched["finished_at"] = self.finished_at
        return enriched

    def _record_result(self) -> None:
        if self._result_store is None or self.finished_monotonic is None:
            return
        self._result_store.record(
            {
                "session_id": self.id,
                "solver": self.solver,
                "size": self.level,
                "seed": self.game.current_seed,
                "moves": self.game.moves,
                "elapsed_seconds": round(self.finished_monotonic - self.started_monotonic, 9),
                "started_at": self.started_at,
                "finished_at": self.finished_at,
            }
        )


class SessionStore:
    """Creates and tracks maze sessions, keyed by their id."""

    def __init__(
        self,
        default_size: int,
        default_seed: int | None = None,
        result_store: ResultStore | None = None,
        clock=time.perf_counter,
    ) -> None:
        self.default_size = default_size
        self.default_seed = default_seed
        self._result_store = result_store
        self._clock = clock
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, level: int, solver: str | None = None) -> Session:
        """Create a timed session for one of the allowed sizes (cell counts)."""
        if level not in ALLOWED_SIZES:
            raise ValueError(f"size must be one of {sorted(ALLOWED_SIZES)}.")
        with self._lock:
            session_id = uuid.uuid4().hex
            session = Session(
                session_id=session_id,
                size=ALLOWED_SIZES[level],
                seed=None,
                solver=solver,
                result_store=self._result_store,
                clock=self._clock,
                level=level,
            )
            self._sessions[session_id] = session
            return session

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            return self._sessions.get(session_id)

    def default(self) -> Session:
        with self._lock:
            session = self._sessions.get(DEFAULT_SESSION_ID)
            if session is None:
                session = self._build_default(self.default_seed)
            return session

    def reset_default(self, seed: int | None = None) -> Session:
        with self._lock:
            return self._build_default(seed)

    def _build_default(self, seed: int | None) -> Session:
        session = Session(
            session_id=DEFAULT_SESSION_ID,
            size=self.default_size,
            seed=seed,
            solver=None,
            result_store=self._result_store,
            clock=self._clock,
        )
        self._sessions[DEFAULT_SESSION_ID] = session
        return session

    def resolve(self, session_id: str | None) -> Session | None:
        """Return the requested session, or the default one when none is given."""
        if session_id is None or session_id == DEFAULT_SESSION_ID:
            return self.default()
        return self.get(session_id)


class MazeAPIHandler(BaseHTTPRequestHandler):
    store: SessionStore
    result_store: ResultStore | None

    server_version = "MazeAPI/2.0"

    # ----- routing -------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/":
            if self._wants_html(params):
                self._html_response(HTTPStatus.OK, self._home_html())
            else:
                self._json_response(HTTPStatus.OK, self._index_payload())
            return

        if parsed.path in ("/health", "/healthz"):
            self._json_response(HTTPStatus.OK, {"status": "ok"})
            return

        if parsed.path == "/state":
            session = self._require_session(self._single(params, "session"))
            if session is not None:
                self._json_response(HTTPStatus.OK, session.state())
            return

        if parsed.path == "/move":
            session = self._require_session(self._single(params, "session"))
            if session is None:
                return
            direction = self._single(params, "direction")
            if direction is None:
                self._json_response(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "Query string must include direction."},
                )
                return
            status, payload = session.move(direction)
            self._json_response(status, payload)
            return

        if parsed.path == "/ascii":
            session = self._require_session(self._single(params, "session"))
            if session is not None:
                self._text_response(HTTPStatus.OK, session.ascii_maze())
            return

        if parsed.path in ("/leaderboard", "/results"):
            payload = self._leaderboard_payload(params)
            if self._wants_html(params):
                self._html_response(HTTPStatus.OK, self._leaderboard_html(payload))
            else:
                self._json_response(HTTPStatus.OK, payload)
            return

        self._json_response(HTTPStatus.NOT_FOUND, {"error": "Endpoint not found."})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        body = self._read_json_body()
        if not isinstance(body, dict):
            body = {}

        if parsed.path in ("/init", "/start"):
            self._handle_init(body)
            return

        if parsed.path == "/move":
            session = self._require_session(body.get("session"))
            if session is None:
                return
            direction = body.get("direction")
            if direction is None:
                self._json_response(
                    HTTPStatus.BAD_REQUEST,
                    {"error": 'JSON body must include "direction".'},
                )
                return
            status, payload = session.move(direction)
            self._json_response(status, payload)
            return

        if parsed.path == "/moves":
            session = self._require_session(body.get("session"))
            if session is None:
                return
            moves = self._parse_moves(body.get("moves"))
            if moves is None:
                self._json_response(
                    HTTPStatus.BAD_REQUEST,
                    {"error": '"moves" must be a string like "NNESW" or a list of directions.'},
                )
                return
            status, payload = session.move_batch(moves)
            self._json_response(status, payload)
            return

        if parsed.path == "/reset":
            seed = body.get("seed")
            if seed is not None and not isinstance(seed, int):
                self._json_response(
                    HTTPStatus.BAD_REQUEST,
                    {"error": 'If provided, "seed" must be an integer.'},
                )
                return
            session = self.store.reset_default(seed=seed)
            payload = session.state()
            payload["message"] = "Default session reset."
            self._json_response(HTTPStatus.OK, payload)
            return

        self._json_response(HTTPStatus.NOT_FOUND, {"error": "Endpoint not found."})

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ----- handlers ------------------------------------------------------
    def _handle_init(self, body: dict[str, Any]) -> None:
        allowed = sorted(ALLOWED_SIZES)

        if "seed" in body:
            self._json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": "/init does not accept a seed; every maze is random."},
            )
            return

        size = body.get("size")
        if size not in ALLOWED_SIZES:
            self._json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": f'"size" is required and must be one of {allowed}.'},
            )
            return

        solver = body.get("solver")
        if solver is not None and not isinstance(solver, str):
            self._json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": 'If provided, "solver" must be a string.'},
            )
            return

        session = self.store.create(level=size, solver=solver)
        payload = session.state()
        payload["message"] = (
            "Session created. Send moves to /move with this session_id; "
            "the timer is running and the elapsed time is returned when you exit."
        )
        payload["started_at_iso"] = _now_iso(session.started_at)
        self._json_response(HTTPStatus.CREATED, payload)

    def _leaderboard_payload(self, params: dict[str, list[str]]) -> dict[str, Any]:
        if self.result_store is None:
            return {"leaderboard": [], "message": "Result persistence is disabled."}

        size = self._int_param(params, "size")
        seed = self._int_param(params, "seed")
        order = self._single(params, "order") or "time"
        if order not in ("time", "moves"):
            order = "time"
        limit = self._int_param(params, "limit") or 20
        limit = max(1, min(limit, 100))

        entries = self.result_store.leaderboard(size=size, seed=seed, order=order, limit=limit)
        return {
            "order": order,
            "size": size,
            "seed": seed,
            "count": len(entries),
            "leaderboard": entries,
        }

    def _index_payload(self) -> dict[str, Any]:
        return {
            "message": "Maze API is running.",
            "allowed_sizes": {
                str(level): {"grid": grid, "cells": grid * grid}
                for level, grid in sorted(ALLOWED_SIZES.items())
            },
            "endpoints": {
                "GET /health": "Liveness check used by the host.",
                "POST /init": 'Start a timed run. JSON body: {"size": 1000|10000|100000, "solver": "name"}. No seed. Returns a session_id.',
                "GET /state?session=ID": "Current position, elapsed time and what is in N/S/E/W.",
                "POST /move": 'Move with JSON body {"session": "ID", "direction": "N"}. The exit move returns the elapsed time.',
                "GET /move?session=ID&direction=N": "Same move endpoint via query string.",
                "POST /moves": 'Submit a whole path at once: {"session": "ID", "moves": "NNESW..."}. Best for large mazes.',
                "GET /ascii?session=ID": "Render the maze in ASCII (reveals the layout; for debugging).",
                "GET /leaderboard?size=10000&order=time&limit=20": "Compare finished runs by time or moves.",
                "POST /reset": 'Reset the shared default session. Optional JSON body: {"seed": 123}.',
            },
            "notes": [
                '"size" is the number of cells: 1000, 10000 or 100000.',
                "For fair per-solver timing, each solver calls /init and reuses its own session_id.",
                "Omit session on /state, /move and /ascii to use a shared default session for manual play.",
            ],
        }

    # ----- HTML pages ----------------------------------------------------
    def _wants_html(self, params: dict[str, list[str]]) -> bool:
        fmt = self._single(params, "format")
        if fmt == "html":
            return True
        if fmt == "json":
            return False
        return "text/html" in (self.headers.get("Accept") or "")

    def _page(self, title: str, body: str) -> str:
        return (
            "<!doctype html><html lang='es'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>" + html.escape(title) + " · Maze API</title>" + _HTML_STYLE +
            "</head><body><div class='wrap'>" + body + "</div></body></html>"
        )

    def _home_html(self) -> str:
        size_rows = "".join(
            "<tr><td><code>{lvl}</code></td><td>{g}×{g}</td><td>{c:,}</td>"
            "<td><a href='/leaderboard?size={lvl}&format=html'>ver leaderboard →</a></td></tr>".format(
                lvl=lvl, g=grid, c=grid * grid
            )
            for lvl, grid in sorted(ALLOWED_SIZES.items())
        )
        endpoint_rows = "".join(
            "<tr><td><code>{}</code></td><td>{}</td></tr>".format(html.escape(path), html.escape(desc))
            for path, desc in self._index_payload()["endpoints"].items()
        )
        quickstart = html.escape(
            "URL=https://maze-api-1gfd.onrender.com\n"
            "# 1) iniciar partida (devuelve session_id y arranca el cronómetro)\n"
            'curl -s -X POST $URL/init -d \'{"size":1000,"solver":"tu-nombre"}\'\n'
            "# 2) mirar alrededor / moverse\n"
            'curl -s "$URL/state?session=SID"\n'
            'curl -s -X POST $URL/move  -d \'{"session":"SID","direction":"E"}\'\n'
            "# 3) o resolver entero y mandar la ruta en una sola request\n"
            'curl -s -X POST $URL/moves -d \'{"session":"SID","moves":"EENSS..."}\'\n'
            "# 4) comparar\n"
            'curl -s "$URL/leaderboard?size=1000"'
        )
        body = (
            "<h1>🌀 Maze API</h1>"
            "<p class='muted'>Resuelve laberintos contra esta API y compara cuánto demora tu "
            "solver. Tu posición se guarda en el servidor entre requests y el tiempo se mide "
            "desde <code>/init</code> hasta que sales.</p>"
            "<h2>Cómo jugar</h2>"
            "<ol>"
            "<li><b>Inicia una partida:</b> <code>POST /init</code> con "
            "<code>{\"size\": 1000, \"solver\": \"tu-nombre\"}</code>. Te devuelve un "
            "<code>session_id</code> y arranca el cronómetro. Tamaños válidos abajo; sin seed "
            "(cada laberinto es aleatorio).</li>"
            "<li><b>Mira a tu alrededor:</b> cada dirección <code>N/S/E/W</code> es "
            "<code>wall</code> (muro), <code>path</code> (camino) o <code>exit</code> (salida). "
            "Consúltalo en <code>/state</code> o en la respuesta de cada movimiento.</li>"
            "<li><b>Muévete:</b> <code>POST /move</code> con "
            "<code>{\"session\":\"…\",\"direction\":\"N\"}</code>. Chocar un muro responde 409 y "
            "<i>no</i> cuesta movimiento. Tu posición persiste entre requests.</li>"
            "<li><b>Sal:</b> la jugada que cruza la <code>exit</code> devuelve tu "
            "<code>elapsed_seconds</code> y total de movimientos.</li>"
            "<li><b>Compara:</b> mira el <a href='/leaderboard?format=html'>leaderboard</a> de tu "
            "tamaño.</li>"
            "</ol>"
            "<p class='muted'><b>Dos estrategias:</b> paso a paso con <code>/move</code> (un "
            "request por celda) o descargar el laberinto con <code>/ascii</code>, resolverlo y "
            "enviar toda la ruta con <code>POST /moves</code> en una sola request. Para 10⁵ celdas "
            "o más, usa <code>/moves</code>.</p>"
            "<h2>Tamaños y leaderboards</h2>"
            "<table><tr><th>size (celdas)</th><th>grilla</th><th>celdas</th><th>ranking</th></tr>"
            + size_rows +
            "</table>"
            "<p><a href='/leaderboard?format=html'>→ Leaderboard combinado (todos los tamaños)</a></p>"
            "<h2>Endpoints</h2>"
            "<table><tr><th>método y ruta</th><th>descripción</th></tr>" + endpoint_rows + "</table>"
            "<h2>Inicio rápido (curl)</h2>"
            "<pre>" + quickstart + "</pre>"
        )
        return self._page("Inicio", body)

    def _leaderboard_html(self, payload: dict[str, Any]) -> str:
        entries = payload.get("leaderboard", [])
        size = payload.get("size")
        order = payload.get("order", "time")

        def chip(href: str, label: str, selected: bool) -> str:
            cls = "chip sel" if selected else "chip"
            return "<a class='{}' href='{}'>{}</a>".format(cls, href, html.escape(label))

        size_nav = "".join(
            chip("/leaderboard?size={}&order={}&format=html".format(lvl, order), "{:,}".format(lvl), lvl == size)
            for lvl in sorted(ALLOWED_SIZES)
        )
        size_nav += chip("/leaderboard?order={}&format=html".format(order), "todos", size is None)

        base = ("size=" + str(size) + "&") if size else ""
        order_nav = (
            chip("/leaderboard?{}order=time&format=html".format(base), "por tiempo", order == "time")
            + chip("/leaderboard?{}order=moves&format=html".format(base), "por movimientos", order == "moves")
        )

        rows = "".join(
            "<tr><td>{rank}</td><td>{solver}</td><td>{size:,}</td><td>{grid}</td>"
            "<td>{moves:,}</td><td>{sec:.3f}</td><td class='muted'>{when}</td></tr>".format(
                rank=entry.get("rank"),
                solver=html.escape(str(entry.get("solver") or "—")),
                size=entry.get("size", 0),
                grid=entry.get("grid", "—"),
                moves=entry.get("moves", 0),
                sec=entry.get("elapsed_seconds", 0.0),
                when=html.escape(str(entry.get("finished_at_iso", ""))[:19].replace("T", " ")),
            )
            for entry in entries
        ) or "<tr><td colspan='7' class='empty'>Sin corridas todavía.</td></tr>"

        scope = "tamaño {:,}".format(size) if size else "todos los tamaños"
        body = (
            "<h1>🏁 Leaderboard <span class='muted'>· " + scope + "</span></h1>"
            "<p><a href='/?format=html'>← Inicio</a></p>"
            "<p>Tamaño: " + size_nav + "</p>"
            "<p>Orden: " + order_nav + "</p>"
            "<table><tr><th>#</th><th>solver</th><th>size</th><th>grilla</th>"
            "<th>movs</th><th>seg</th><th>terminó (UTC)</th></tr>" + rows + "</table>"
        )
        return self._page("Leaderboard", body)

    # ----- helpers -------------------------------------------------------
    def _require_session(self, session_id: str | None) -> Session | None:
        session = self.store.resolve(session_id)
        if session is None:
            self._json_response(
                HTTPStatus.NOT_FOUND,
                {"error": f"Unknown session_id {session_id!r}. Call /init to create one."},
            )
            return None
        return session

    @staticmethod
    def _parse_moves(raw: Any) -> list[str] | None:
        if isinstance(raw, str):
            return [char for char in raw if not char.isspace()]
        if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
            return raw
        return None

    @staticmethod
    def _single(params: dict[str, list[str]], key: str) -> str | None:
        values = params.get(key)
        if not values:
            return None
        return values[0]

    def _int_param(self, params: dict[str, list[str]], key: str) -> int | None:
        value = self._single(params, key)
        if value is None:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    def _read_json_body(self) -> Any:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length == 0:
            return {}
        raw_body = self.rfile.read(content_length)
        try:
            return json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")

    def _json_response(self, status: int, payload: dict[str, Any]) -> None:
        response = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(response)

    def _text_response(self, status: int, payload: str) -> None:
        response = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(response)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(response)

    def _html_response(self, status: int, payload: str) -> None:
        response = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(response)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))


def build_handler(
    store: SessionStore,
    result_store: ResultStore | None,
) -> type[MazeAPIHandler]:
    class BoundMazeAPIHandler(MazeAPIHandler):
        pass

    BoundMazeAPIHandler.store = store
    BoundMazeAPIHandler.result_store = result_store
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
    db_path: str = ":memory:",
) -> ThreadingHTTPServer:
    result_store = ResultStore(db_path)
    store = SessionStore(default_size=size, default_seed=seed, result_store=result_store)
    handler = build_handler(store, result_store)
    server = ThreadingHTTPServer((host, port), handler)
    server.maze_store = store  # type: ignore[attr-defined]
    server.maze_result_store = result_store  # type: ignore[attr-defined]
    return server


def _env_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return None
    return int(raw)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a maze game controlled through HTTP requests.",
    )
    parser.add_argument(
        "n",
        nargs="?",
        type=positive_int,
        default=_env_int("MAZE_SIZE") or 15,
        help="Size of the maze (n x n). Defaults to MAZE_SIZE or 15.",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("MAZE_HOST", "0.0.0.0"),
        help="Host interface to bind to. Defaults to MAZE_HOST or 0.0.0.0 (required on Render).",
    )
    parser.add_argument(
        "--port",
        type=positive_int,
        default=_env_int("PORT") or 8000,
        help="Port to expose the API on. Defaults to the PORT env var or 8000.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=_env_int("MAZE_SEED"),
        help="Optional seed for the shared default session.",
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("MAZE_DB_PATH", ":memory:"),
        help="SQLite path for the leaderboard. Defaults to MAZE_DB_PATH or :memory:.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    server = create_server(
        args.n,
        host=args.host,
        port=args.port,
        seed=args.seed,
        db_path=args.db,
    )
    print(f"Maze API listening on http://{args.host}:{args.port}")
    print(f"Default maze size: {args.n}x{args.n}")
    print(f"Leaderboard database: {args.db}")
    print("Endpoints: POST /init, POST|GET /move, GET /state, GET /ascii, GET /leaderboard, GET /health")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

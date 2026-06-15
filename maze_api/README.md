# Maze API

A small maze game exposed over HTTP. A "solver" (written by the course)
calls an initialization endpoint, walks the maze by sending move commands,
and the server **persists the player's position between requests** and
**times each run** so different solvers can be compared on a leaderboard.

Built on the Python standard library only — no dependencies — so it deploys
to [Render](https://render.com) without a build step.

## How a run works

1. `POST /init` with one of three sizes → starts a timed run and returns a
   `session_id`. **No seed is accepted: every maze is random.**
2. `POST /move` (repeatedly) → send `N`/`S`/`E`/`W`; the server remembers
   where the player is for that session.
3. When the player steps through the exit, the response **returns the elapsed
   time** and the number of moves, and the run is stored.
4. `GET /leaderboard` → compare finished runs by time or moves.

The solver only sees what is visible from its current cell
(`directions`: each of `N/S/E/W` is `wall`, `path`, or `exit`) plus its
absolute `position` — it must explore to find the exit.

### Sizes

`size` is the **number of cells**, mapped to a square grid:

| `size` | grid | cells |
| --- | --- | --- |
| `1000` | 32×32 | 1024 |
| `10000` | 100×100 | 10000 |
| `100000` | 316×316 | 99856 |
| `1000000` | 1000×1000 | 1000000 |

(A side length of 10⁴ would already be 10⁸ cells, hence the cell-count
convention. `GET /` lists the exact grids.) The maze is stored as one byte per
cell, so even the 10⁶ grid is ~1 MB and builds in a couple of seconds.

> At `100000`+ cells, **submit your path in one request with `POST /moves`**
> instead of one `POST /move` per step — otherwise you make tens of thousands
> of HTTP round trips and you end up timing the network, not your solver.

## Run locally

```bash
# From the repository root:
python -m maze_api                 # server on http://0.0.0.0:8000

# In another terminal, run the example solver:
python -m maze_api.client --url http://127.0.0.1:8000 --size 1000 --solver dfs-explorer
```

## How to play

Open the service URL in a browser (e.g. `https://maze-api-1gfd.onrender.com/`)
for an HTML home with these instructions and links to the live leaderboards.
The API itself still returns JSON — the HTML is served only to browsers (or
with `?format=html`).

The rules: from your current cell you only see what is immediately around you
— each of `N/S/E/W` is `wall`, `path` or `exit`. You move one cell at a time;
the server remembers your position. You win when you step through the `exit`.

A session played by hand with `curl` (here `URL=http://127.0.0.1:8000`):

**1. Start a run.** Pick a size and get a `session_id` (the timer starts now):

```bash
$ curl -s -X POST $URL/init -d '{"size":1000,"solver":"me"}'
{ "session_id": "ec33...9eb", "size": 1000, "grid": 32, "cells": 1024,
  "status": "playing", "position": {"row":7,"col":7},
  "directions": {"N":"wall","S":"wall","E":"path","W":"wall"},
  "allowed_moves": ["E"], "elapsed_seconds": 0.00002 }
```

**2. Look around / check where you are** (position persists between requests):

```bash
$ curl -s "$URL/state?session=ec33...9eb"
# same shape: position {7,7}, directions N=wall S=wall E=path W=wall
```

**3. Move.** Only `path`/`exit` directions work. Bumping a wall is rejected and
does **not** cost a move:

```bash
$ curl -s -X POST $URL/move -d '{"session":"ec33...9eb","direction":"N"}'
{ "error": "Cannot move N; there is a wall there.", ... }       # HTTP 409

$ curl -s -X POST $URL/move -d '{"session":"ec33...9eb","direction":"E"}'
{ "status":"playing", "position":{"row":7,"col":8}, "moves":1,
  "directions": {...} }                                          # HTTP 200
```

**4. Keep moving** (read `directions`, pick a `path`, repeat). The move that
steps through the `exit` returns your time:

```bash
{ "status":"won", "moves":606, "elapsed_seconds":0.486,
  "message":"You escaped in 0.486 s after 606 moves." }
```

**5. Compare runs:**

```bash
$ curl -s "$URL/leaderboard?size=1000&order=time"
# { "leaderboard": [ {"rank":1,"solver":"me","moves":606,"elapsed_seconds":0.486}, ... ] }
```

### Two ways to play (and why `/moves` exists)

The same maze, two strategies — measured on a 100×100 (`size=10000`) grid:

| Strategy | Game requests | Moves | What it measures |
| --- | --- | --- | --- |
| Step by step, blind (`/move`) | 8371 | 8369 | exploration + the network |
| Download + solve + submit path (`/moves`) | 1 | 2183 (shortest) | your algorithm |

A blind walker reacts only to what it sees and wanders; a solver that fetches
`GET /ascii`, computes the shortest path, and submits it with one `POST /moves`
finishes in far fewer moves and round trips. The leaderboard rewards the better
solver. At `100000`+ cells the step-by-step route is impractical (tens of
thousands of HTTP calls), so use `/moves`.

## Endpoints

| Method & path | Description |
| --- | --- |
| `GET /health` | Liveness check (used by Render). |
| `POST /init` | Start a timed run. JSON body `{"size": 1000\|10000\|100000, "solver": "name"}` (no seed). Returns a `session_id`. |
| `GET /state?session=ID` | Current position, elapsed time and what is in N/S/E/W. |
| `POST /move` | Body `{"session": "ID", "direction": "N"}`. The exit move returns the elapsed time. |
| `GET /move?session=ID&direction=N` | Same move via query string. |
| `POST /moves` | Submit a whole path at once: `{"session": "ID", "moves": "NNESW..."}`. Recommended for large mazes. |
| `GET /ascii?session=ID` | ASCII render (reveals the layout — debugging only). |
| `GET /leaderboard?size=10000&order=time&limit=20` | Finished runs sorted by `time` or `moves`. |
| `POST /reset` | Reset the shared default session (manual play). Optional `{"seed": 123}`. |

For fair per-solver timing, each solver calls `/init` and reuses its own
`session_id`. Omit `session` on `/state`, `/move` and `/ascii` to use a shared
default session for manual `curl` play.

### Example with curl

```bash
URL=https://your-app.onrender.com
SID=$(curl -s -X POST $URL/init -d '{"size":1000,"solver":"me"}' | python -c "import sys,json;print(json.load(sys.stdin)['session_id'])")
curl -s "$URL/state?session=$SID"
curl -s -X POST $URL/move -d "{\"session\":\"$SID\",\"direction\":\"N\"}"
curl -s "$URL/leaderboard?size=1000&order=time"
```

## Writing a solver

Just talk to the API. The included `MazeClient` (`maze_api/client.py`) ships
two reference solvers:

- `solve_by_exploration` — walks step by step using only what is visible
  (one `/move` per step). Fine for small mazes.
- `solve_by_download` — fetches the whole maze with `GET /ascii`, solves it
  locally, and submits the path with a single `POST /moves`. The practical
  approach at scale.

```python
from maze_api.client import MazeClient, solve_by_download

client = MazeClient("https://your-app.onrender.com", solver="my-solver")
initial = client.init(size=1000000)          # 1000, 10000, 100000 or 1000000
final = solve_by_download(client, initial)   # one /moves request
print(final["moves"], final["elapsed_seconds"])
```

Or run the bundled demo:

```bash
python -m maze_api.client --url $URL --size 1000000 --mode download --solver my-solver
```

Timing is measured **server-side**, from `/init` to the winning move, so it
includes network latency and the solver's thinking time — exactly what you
want when comparing solvers against the same hosted maze size. With `/moves`
the network is a single round trip, so the leaderboard reflects the solver's
compute (e.g. shortest-path vs. a path that wanders).

## Deploy to Render

The repo ships a `render.yaml` blueprint at its root (paid `starter`
instance + a 1 GB disk for the leaderboard database).

1. Push this repository to GitHub/GitLab.
2. On Render: **New → Blueprint**, point it at the repo. It reads
   `render.yaml` and creates a Web Service.
   - Build command: `pip install -r requirements.txt` (no-op, no deps).
   - Start command: `python -m maze_api --host 0.0.0.0 --port $PORT`.
   - Health check: `/health`.
   - The disk is mounted at `/var/data` and `MAZE_DB_PATH=/var/data/maze.db`
     so the leaderboard survives restarts and redeploys.

### Configuration (environment variables)

| Variable | Default | Purpose |
| --- | --- | --- |
| `PORT` | `8000` | Port to bind (Render sets this automatically). |
| `MAZE_HOST` | `0.0.0.0` | Bind address. |
| `MAZE_SIZE` | `15` | Grid side for the manual default session only (not `/init`). |
| `MAZE_DB_PATH` | `:memory:` | SQLite file for the leaderboard. |

### Operational notes

- The `starter` instance does not spin down on idle, so timings stay
  comparable and long `100000` runs are not interrupted.
- **Active sessions live in memory**: if the service restarts mid-run, open
  sessions are lost (start a new `/init`). Completed results persist on the
  mounted disk.
- Mazes are stored compactly (~1 byte/cell), so a `1000000` session is only
  ~1 MB; `/init` for it takes a couple of seconds to generate. Size the
  instance for the number of concurrent students you expect.

## Tests

```bash
python -m unittest maze_api.test_api maze_api.test_solver
```

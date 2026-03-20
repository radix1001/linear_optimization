# Opti

This repository collects beginner-level notebooks on mixed-integer linear programming.

Several introductory notebooks will be added over time, covering small models, exact methods, and implementation ideas for students getting started with MILP.

## Notebook Index

1. [integer_programming_pure_python.ipynb](./integer_programming_pure_python.ipynb)
   Introductory notebook with five exact methods for small integer optimization problems written in pure Python.

2. [integer_programming_ampl_python.ipynb](./integer_programming_ampl_python.ipynb)
   Introductory notebook that models and solves the same carpenter problem in AMPL from Python using `amplpy`.

3. [bus_assignment_pure_python.ipynb](./bus_assignment_pure_python.ipynb)
   Exact-methods notebook for the cyclic bus assignment problem in pure Python, including multiple optimal solutions analysis.

4. [bus_assignment_ampl_python.ipynb](./bus_assignment_ampl_python.ipynb)
   AMPL-from-Python notebook for the same bus assignment model, including an alternative optimum and textbook-solution verification.

## Problems Covered So Far

- Two-product carpenter problem
- Three-product carpenter problem
- Bus assignment problem with 8-hour consecutive shifts

## Methods Included

- Naive enumeration
- Backtracking
- Optimized enumeration
- Constraint-driven reduced search
- Dynamic programming
- Branch and Bound
- AMPL modeling with Python (`amplpy`)

## Maze API

This repository also includes a small local Python game API inside the [`maze_api/`](./maze_api/) folder.

The app generates a perfect `n x n` maze with exactly one exit to the outside, starts the player at the farthest cell from that exit, and exposes the game over HTTP.

The active `seed` is always returned by the API, so if you launch again with the same `n` and `seed`, you get the same maze.

### Run

```bash
python3 -m maze_api 8
```

Optional flags:

- `--host 127.0.0.1`
- `--port 8000`
- `--seed 42` for a reproducible maze

Example:

```bash
python3 -m maze_api 10 --port 9000
```

Same maze again:

```bash
python3 -m maze_api 10 --port 9000 --seed 42
```

### API

Get the current state:

```bash
curl http://127.0.0.1:8000/state
```

The response includes the current `seed`.

Move north, south, east, or west with `N`, `S`, `E`, `W`:

```bash
curl "http://127.0.0.1:8000/move?direction=N"
```

Or with `POST`:

```bash
curl -X POST http://127.0.0.1:8000/move \
  -H "Content-Type: application/json" \
  -d '{"direction":"E"}'
```

Reset the maze:

```bash
curl -X POST http://127.0.0.1:8000/reset
```

Reset with a fixed seed:

```bash
curl -X POST http://127.0.0.1:8000/reset \
  -H "Content-Type: application/json" \
  -d '{"seed":42}'
```

Print the generated maze in ASCII:

```bash
curl http://127.0.0.1:8000/ascii
```

### Response Shape

`GET /state` returns a payload like:

```json
{
  "size": 8,
  "seed": 42,
  "status": "playing",
  "position": { "row": 5, "col": 1 },
  "start": { "row": 5, "col": 1 },
  "moves": 3,
  "directions": {
    "N": "wall",
    "S": "path",
    "E": "path",
    "W": "wall"
  },
  "allowed_moves": ["S", "E"],
  "message": null
}
```

`directions` always reports what exists in each direction:

- `wall`: blocked
- `path`: open corridor
- `exit`: the unique way out of the maze

### Files

- [`maze_api/server.py`](./maze_api/server.py): generator, game state and HTTP API
- [`maze_api/__main__.py`](./maze_api/__main__.py): lets you run it with `python3 -m maze_api`
- [`maze_api/solver.py`](./maze_api/solver.py): pure Python ASCII maze solver
- [`maze_api/test_api.py`](./maze_api/test_api.py): API smoke tests
- [`maze_api/test_solver.py`](./maze_api/test_solver.py): solver tests

## Maze Solver

The package also includes a pure Python solver that reads an ASCII maze, finds a path with breadth-first search, prints the move sequence using `N`, `S`, `E`, `W`, and reports the elapsed solve time.

### Solve From a File

Save a maze to a text file and solve it:

```bash
python3 -m maze_api.solver --file maze.txt
```

### Solve From Standard Input

You can pipe the ASCII maze directly:

```bash
python3 -m maze_api.solver --stdin < maze.txt
```

### Generate and Solve in One Step

The solver can also generate a maze with the same generator used by the API and solve it immediately:

```bash
python3 -m maze_api.solver --size 10 --seed 42
```

### Solve a Maze Exported by the API

```bash
curl http://127.0.0.1:8000/ascii > maze.txt
python3 -m maze_api.solver --file maze.txt
```

### Example Output

```text
Source: generated size=10 seed=42
Moves: EESSSENNW
Move count: 9
Elapsed seconds: 0.000123456
Solved maze:
+---+---+---+
| P . . X   |
+---+   +   +
|           |
+---+---+---+
```

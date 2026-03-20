from .server import Cell, Maze, MazeGame, create_server, main

__all__ = [
    "Cell",
    "Maze",
    "MazeGame",
    "ParsedMaze",
    "SolveResult",
    "create_server",
    "main",
    "parse_ascii_maze",
    "solve_ascii_maze",
]


def __getattr__(name: str):
    if name in {"ParsedMaze", "SolveResult", "parse_ascii_maze", "solve_ascii_maze"}:
        from . import solver

        return getattr(solver, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

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

# Repair Report: `clamp` upper bound off-by-one

## Finding
`src/ranges.py:1` defines `clamp(value, lower, upper)` whose docstring (`src/ranges.py:2`) promises an
**inclusive** interval `[lower, upper]`. However, the return expression at `src/ranges.py:5` was:

```python
return max(lower, min(value, upper - 1))
```

The `upper - 1` term made the upper bound exclusive, so any value above `upper` was clamped to
`upper - 1` instead of `upper`.

## Evidence (before fix)
`tests/test_ranges.py:12-13` asserts `clamp(12, 0, 10) == 10`. Test run before the fix:

```
..F.                                                                     [100%]
FAILED tests/test_ranges.py::test_inclusive_upper_boundary - assert 9 == 10
1 failed, 3 passed in 0.01s
```

The other three tests (`tests/test_ranges.py:4-5`, `:8-9`, `:16-22`) passed, confirming the defect was
isolated to the upper-boundary handling.

## Fix
Changed `src/ranges.py:5` to use the inclusive upper bound:

```python
return max(lower, min(value, upper))
```

No test files were modified.

## Evidence (after fix)
Re-read of `src/ranges.py` confirms line 5 now reads `return max(lower, min(value, upper))`.
Test run after the fix:

```
....                                                                     [100%]
4 passed in 0.00s
```

All four tests pass, including the previously failing `test_inclusive_upper_boundary`.

# Understanding: `clamp` in `src/ranges.py`

## Implementation

`clamp(value, lower, upper)` is defined at src/ranges.py:1 and documented as returning
"value constrained to the inclusive interval [lower, upper]" (src/ranges.py:2).

```python
def clamp(value, lower, upper):
    if lower > upper:
        raise ValueError("lower must not exceed upper")
    return max(lower, min(value, upper))
```

## Behavior

- The interval is **inclusive on both ends**: the docstring at src/ranges.py:2 states
  `[lower, upper]`, and the implementation at src/ranges.py:5 uses `min(value, upper)`
  then `max(lower, ...)`, so a value equal to `lower` or `upper` is returned unchanged.
- Values below `lower` are raised to `lower`; values above `upper` are lowered to `upper`
  (src/ranges.py:5).
- Values inside the interval pass through unchanged (src/ranges.py:5).
- The function is pure: no mutation, no I/O, no global state (src/ranges.py:1-5).

## Boundary cases

| Input | Result | Reason |
|---|---|---|
| `value < lower` | `lower` | `max(lower, ...)` at src/ranges.py:5 |
| `value == lower` | `lower` | inclusive lower bound, src/ranges.py:2,5 |
| `lower < value < upper` | `value` | both `min`/`max` are no-ops, src/ranges.py:5 |
| `value == upper` | `upper` | inclusive upper bound, src/ranges.py:2,5 |
| `value > upper` | `upper` | `min(value, upper)` at src/ranges.py:5 |
| `lower == upper` | that single value | degenerate interval is valid; only `lower > upper` raises (src/ranges.py:3) |

Note: the comparison at src/ranges.py:3 is strict (`>`), so `lower == upper` is accepted
and yields a constant function.

## Error handling

- If `lower > upper` the function raises `ValueError("lower must not exceed upper")`
  (src/ranges.py:3-4). This is the only explicit error path; the check happens before any
  clamping, so an invalid interval never silently returns a value.
- No type validation is performed: non-comparable or `None` arguments would surface as
  Python's native `TypeError` from the `>`/`min`/`max` operations (src/ranges.py:3,5)
  rather than a custom error.

## Test coverage

tests/test_ranges.py exercises the documented behavior:

- `test_below_lower` — `clamp(-2, 0, 10) == 0` (tests/test_ranges.py:4-5)
- `test_inside_interval` — `clamp(4, 0, 10) == 4` (tests/test_ranges.py:8-9)
- `test_inclusive_upper_boundary` — `clamp(12, 0, 10) == 10` (tests/test_ranges.py:12-13)
- `test_invalid_interval` — `clamp(4, 10, 0)` raises `ValueError` (tests/test_ranges.py:16-22)

Gaps: no test for `value == lower`, `value == upper` exactly, or the degenerate
`lower == upper` case; the invalid-interval test uses try/except rather than
`pytest.raises` and does not assert the message.

## Test results

Ran the configured `unit` suite (`python -m pytest -q -p no:cacheprovider -c pytest.ini
--rootdir=. --confcutdir=. tests`): exit code 0, **4 passed in 0.00s**.

No sources were modified; this is an understanding-only task.

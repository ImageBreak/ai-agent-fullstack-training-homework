# Repair report

src/ranges.py:1 defines clamp with an inclusive lower and upper bound. src/ranges.py:5 constrains the value using min/max. Invalid intervals raise ValueError.

The configured unit suite was executed against the current source and all four tests passed. The upper-bound off-by-one defect was repaired; see changes.diff.

# Python AST Duplicate Detector — Design

## Goal

Create a standalone, standard-library Python command-line program at
`/home/dannyb/sources/dupes.py`. When run, it recursively scans the current
working directory for Python source files and reports duplicated contiguous
statement sequences, including duplicates in different files.

## Command-line interface

```text
python /home/dannyb/sources/dupes.py [PATH] [--min-statements N]
```

- `PATH` is optional and defaults to `.`.
- `--min-statements N` is optional and defaults to `10`; `N` must be a
  positive integer.
- The scan includes `*.py` files beneath the selected path.
- The program uses only the Python standard library.

## Duplicate definition

A duplicate is a contiguous sequence of at least `N` statements from the same
statement-list context (module, class, function, loop, conditional branch,
exception handler, or similar AST body list) whose canonical forms match.

The canonical form:

- ignores identifier spelling, including names in assignments, attribute
  access, parameters, and keyword names;
- replaces all literal values with their literal kind;
- discards source locations and formatting;
- normalizes safe, local control-flow equivalents:
  - removes double negation;
  - canonicalizes commutative boolean groups and equality/inequality operand
    order where safe;
  - treats an `if` with a negated condition and swapped `body`/`orelse` as the
    same construct as its non-negated counterpart.

The detector intentionally does not attempt arbitrary program equivalence,
control-flow graph equivalence across loops/exceptions, or side-effect-aware
algebraic rewrites. This keeps false positives and implementation complexity
bounded.

## Architecture

1. **Discovery** recursively finds Python files below the supplied path.
   Directories commonly containing generated or virtual-environment content
   (`.git`, `__pycache__`, `.venv`, `venv`, `build`, and `dist`) are skipped.
2. **Parsing** calls `ast.parse` for each discovered file. Syntax errors are
   recorded and reported to stderr without terminating the full scan.
3. **Candidate collection** walks every AST body-list and yields every
   contiguous statement sequence whose length is at least the configured
   minimum. A candidate retains its file path and first/last source line.
4. **Canonicalization** transforms a candidate into a hashable structural
   representation under the normalization rules above.
5. **Grouping and reporting** hashes canonical representations, retains only
   groups with two or more distinct locations, and prints them deterministically
   with statement count and locations. A no-duplicates result exits successfully
   with a clear message.

## Errors and edge cases

- Invalid `--min-statements` input produces argparse usage output and a
  nonzero exit.
- Missing paths or paths that are not directories produce a clear nonzero
  error.
- Files that cannot be decoded or parsed are skipped and named on stderr.
- A duplicate is reported once per group; overlapping candidate sequences are
  retained only when they differ in size or location, making the output
  complete but deterministic.

## Testing

Tests will use `unittest` with temporary directories and subprocess invocation
of the completed script. Coverage will include:

- duplicate sequences in separate files;
- identifier and literal normalization;
- inverted-condition/swapped-branch equivalence;
- default and custom minimum statement counts;
- source-location output;
- malformed Python files being skipped; and
- CLI validation failures.

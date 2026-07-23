# SKILL: argparse-cli

## Description
Build command-line interfaces using Python's `argparse` module with subcommands, proper exit codes, and helpful error messages.

## Requirements

### Core Implementation
1. **Use `argparse` with subcommands** via `add_subparsers()`
2. **Exit codes:**
   - Success: exit code 0
   - Missing/unknown arguments: exit code 2 (default argparse behavior)
   - No tracebacks on user errors
3. **`--help` behavior:**
   - Exit code 0
   - List all subcommands with descriptions
4. **Each subcommand:**
   - Performs exactly as specified
   - Prints clean output (no extra debug info)

### Implementation Pattern
```python
import argparse
import sys

def main():
    parser = argparse.ArgumentParser(description="Tool description")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Add subcommands
    parser_sub = subparsers.add_parser("subcommand", help="Subcommand description")
    parser_sub.add_argument("arg", help="Argument description")
    
    args = parser.parse_args()
    
    if args.command == "subcommand":
        # Implement subcommand logic
        result = process(args.arg)
        print(result)
    else:
        parser.print_help()
        sys.exit(2)

if __name__ == "__main__":
    main()
```

## Validation Steps
1. Run with `--help` → exit 0, lists subcommands
2. Run with valid subcommand and args → exit 0, clean output
3. Run with missing args → exit 2, argparse error message
4. Run with unknown subcommand → exit 2, argparse error message
5. Run with invalid args → exit 2, argparse error message

## Guardrails
- Never catch `SystemExit` from argparse
- Never print tracebacks for user errors
- Always use `sys.exit()` for custom exits
- Keep output minimal: only print what's specified
- Use `dest="command"` for subcommand dispatch
- Set `required=True` on subparsers if all subcommands are mandatory

## Contingencies
- **Missing subcommand:** argparse shows error and exits 2
- **Extra arguments:** argparse shows error and exits 2
- **Invalid argument types:** argparse handles type conversion and errors
- **Help requested:** argparse handles `-h`/`--help` automatically

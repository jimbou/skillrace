# SKILL: argparse-cli-implementation

## Description
This skill teaches how to implement a command-line interface (CLI) using Python's `argparse` module with subcommands, proper exit codes, and helpful error messages.

## Key Components
- Use Python's `argparse` module to create a CLI with subcommands
- Implement proper exit codes (0 for success, non-zero for errors)
- Handle unknown/missing arguments without tracebacks
- Provide helpful error messages
- Support `--help` flag that exits with code 0 and lists subcommands
- Each subcommand should perform its specified task and print clean results

## Implementation Steps
1. Import the `argparse` module
2. Create a top-level parser
3. Add subparsers for each subcommand
4. Configure each subcommand with its specific arguments
5. Implement argument parsing and validation
6. Handle different scenarios (valid args, missing args, bad args)

## Contingencies
- **Valid arguments**: Execute the corresponding subcommand and exit with code 0
- **Missing arguments**: Display appropriate error message and exit with non-zero code
- **Unknown arguments**: Display "unrecognized arguments" error and exit with non-zero code
- **Invalid arguments**: Display validation error and exit with non-zero code
- **Help requests**: Display help information and exit with code 0

## Validation Steps
1. Verify the CLI accepts valid arguments for each subcommand
2. Confirm invalid arguments produce appropriate error messages
3. Check that missing arguments result in helpful error messages
4. Ensure `--help` displays all subcommands and exits with code 0
5. Verify each subcommand produces clean output without extraneous text

## Guardrails
- Never use exit codes other than 0 for success or non-zero for errors
- Avoid tracebacks in error output; use user-friendly messages instead
- Ensure each subcommand does exactly what its specification requires
- Keep output clean and formatted appropriately
- Don't assume argument values; validate them before use
- Handle edge cases gracefully without crashing

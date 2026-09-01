# SevenTPrep 0.2.1 startup hotfix

Version 0.2.0 imported the complete preprocessing stack while Python imported
`seventprep.cli`. As a result, an incompatible or missing Pydra installation
could stop every command at the generated console-script line
`from seventprep.cli import app`, even when the requested command was only
`inventory`.

Version 0.2.1:

- loads command-specific modules only after Typer has selected a command;
- adds `seventprep doctor` for interpreter, package, Pydra-API, and executable
  diagnostics;
- reports dependency import failures without hiding the missing module name;
- uses `seventprep.cli:main` as the console-script entry point.

To show a full Python traceback rather than the concise dependency message, set:

```bash
export SEVENTPREP_DEBUG_IMPORTS=1
```

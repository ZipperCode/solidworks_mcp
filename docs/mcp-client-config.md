# MCP Client Configuration

This server uses stdio transport through the official Python MCP SDK.

## Discovery surfaces

The server keeps the executable tool surface limited to the six high-level
tools, but also exposes read-only planning helpers:

- resource `solidworks://capabilities`
- resource `solidworks://capabilities/{category}`
- prompt `plan_solidworks_operation`

Use these discovery surfaces to decide whether a requested SolidWorks ability is
currently executable, planned, research-only or blocked.  Only executable
operations accepted by `validate_model_plan` should be submitted to
`execute_model_plan`.

## Local mock mode

Use mock mode on macOS or Linux while developing prompts, schemas and client
wiring:

```json
{
  "mcpServers": {
    "solidworks-mcp": {
      "command": "uv",
      "args": ["run", "solidworks-mcp"],
      "env": {
        "SOLIDWORKS_MCP_ADAPTER": "mock",
        "SOLIDWORKS_MCP_OUTPUT_DIR": "outputs"
      }
    }
  }
}
```

## Windows SolidWorks mode

Run this on a Windows machine with SolidWorks installed:

```json
{
  "mcpServers": {
    "solidworks-mcp": {
      "command": "C:\\\\path\\\\to\\\\venv\\\\Scripts\\\\python.exe",
      "args": ["-m", "solidworks_mcp"],
      "env": {
        "SOLIDWORKS_MCP_ADAPTER": "solidworks",
        "SOLIDWORKS_MCP_OUTPUT_DIR": "C:\\\\SolidWorksMCP\\\\outputs",
        "SOLIDWORKS_MCP_PART_TEMPLATE": "C:\\\\ProgramData\\\\SOLIDWORKS\\\\SOLIDWORKS 2025\\\\templates\\\\Part.prtdot",
        "SOLIDWORKS_MCP_DRAWING_TEMPLATE": "C:\\\\ProgramData\\\\SOLIDWORKS\\\\SOLIDWORKS 2025\\\\templates\\\\Drawing.drwdot",
        "SOLIDWORKS_MCP_MACRO_FALLBACK": "0",
        "SOLIDWORKS_MCP_DEBUG_LEVEL": "basic"
      }
    }
  }
}
```

The real adapter requires `pip install "solidworks-mcp[windows]"` so pywin32
and comtypes are available.

## Windows smoke test

After configuring templates and installing the `windows` extra, run:

```powershell
$env:SOLIDWORKS_MCP_ADAPTER = "solidworks"
$env:SOLIDWORKS_MCP_OUTPUT_DIR = "C:\\SolidWorksMCP\\outputs"
python scripts\smoke_mounting_plate.py
```

For a local dry-run without SolidWorks:

```bash
python3 scripts/smoke_mounting_plate.py --mock
```

The smoke script executes `examples/mounting_plate_plan.json` with
`confirmed=true`, prints the generated run directory and writes the debug
artifact set inside that run directory:

- `plan.normalized.json`
- `execution_report.json`
- `events.jsonl`
- `environment.json`
- `artifacts.json`

For offline diagnosis after copying a run directory back:

```bash
python3 scripts/diagnose_run.py <run_dir>
```

import importlib.util
import sys
from pathlib import Path


def _load_tool_module(subdir: Path):
    """Load a tool.py from a subdirectory, even if the dir name has hyphens."""
    module_key = f"_tool_{subdir.name.replace('-', '_')}"
    if module_key in sys.modules:
        return sys.modules[module_key]
    spec = importlib.util.spec_from_file_location(module_key, subdir / "tool.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_key] = module
    spec.loader.exec_module(module)
    return module


def load_all_definitions() -> list:
    """Return all tool DEFINITION dicts for passing to the Claude API tools= parameter."""
    definitions = []
    tools_dir = Path(__file__).parent
    for subdir in sorted(tools_dir.iterdir()):
        if subdir.is_dir() and (subdir / "tool.py").exists():
            module = _load_tool_module(subdir)
            if hasattr(module, "DEFINITION"):
                definitions.append(module.DEFINITION)
    return definitions


def dispatch(tool_name: str, tool_input: dict):
    """Route a tool_use block from Claude to the correct tool's run() function."""
    tools_dir = Path(__file__).parent
    for subdir in tools_dir.iterdir():
        if subdir.is_dir() and (subdir / "tool.py").exists():
            module = _load_tool_module(subdir)
            if getattr(module, "DEFINITION", {}).get("name") == tool_name:
                return module.run(**tool_input)
    raise ValueError(f"Unknown tool: {tool_name}")


def load_tool(tool_dir_name: str):
    """Load a single tool module by its directory name (e.g. 'tool-list-folder-files')."""
    tools_dir = Path(__file__).parent
    subdir = tools_dir / tool_dir_name
    if not (subdir / "tool.py").exists():
        raise FileNotFoundError(f"Tool not found: {tool_dir_name}")
    return _load_tool_module(subdir)

#!/usr/bin/env python3
"""
Disable write-tool @mcp.tool() registrations in robcerda/monarch-mcp-server.

Run from the repo root:
    python3 disable-writes.py

Walks src/monarch_mcp_server/tools/*.py and comments out @mcp.tool()
decorators whose immediately-following `async def` matches WRITE_TOOLS.
Read tools are left alone. Function bodies stay intact (just no longer
registered with the MCP server), so the diff is minimal and reversible.
"""

import re
from pathlib import Path

WRITE_TOOLS = {
    # tags
    "set_transaction_tags",
    "create_transaction_tag",
    "add_transaction_tag",
    # rules
    "create_transaction_rule",
    "update_transaction_rule",
    "delete_transaction_rule",
    # accounts
    "upload_account_balance_history",
    # transactions
    "create_transaction",
    "update_transaction",
    "categorize_transaction",
    "update_transaction_notes",
    "mark_transaction_reviewed",
    "bulk_categorize_transactions",
    "delete_transaction",
    # categories
    "create_transaction_category",
    # budgets
    "set_budget_amount",
    # splits
    "split_transaction",
}

TOOLS_DIR = Path("src/monarch_mcp_server/tools")

if not TOOLS_DIR.is_dir():
    raise SystemExit(
        f"Run this script from the repo root. Expected {TOOLS_DIR} relative to cwd."
    )

disabled = []

for py_file in sorted(TOOLS_DIR.glob("*.py")):
    text = py_file.read_text()
    lines = text.split("\n")
    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if re.match(r"^\s*@mcp\.tool\(\)\s*$", line):
            # Find the next non-blank, non-decorator line
            j = i + 1
            while j < len(lines) and (
                lines[j].strip() == "" or lines[j].strip().startswith("@")
            ):
                j += 1
            if j < len(lines):
                m = re.match(r"^\s*async def (\w+)\s*\(", lines[j])
                if m and m.group(1) in WRITE_TOOLS:
                    indent = re.match(r"^(\s*)", line).group(1)
                    new_lines.append(f"{indent}# @mcp.tool()  # disabled: write op")
                    disabled.append(f"{py_file.name}:{i+1} -> {m.group(1)}")
                    i += 1
                    continue
        new_lines.append(line)
        i += 1
    py_file.write_text("\n".join(new_lines))

print(f"Disabled {len(disabled)} write-tool registrations:")
for d in disabled:
    print(f"  {d}")

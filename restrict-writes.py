#!/usr/bin/env python3
"""
Restrict Monarch MCP writes to a curated SAFE subset.

Successor to disable-writes.py. That script stripped ALL 17 write tools
for a read-only deployment. This one keeps a curated set of *reversible*
edit tools registered and disables only the higher-blast-radius ones, so
Aaron can let Claude edit his Monarch data (categorize / notes / tags /
split / mark-reviewed) without exposing destructive or future-affecting
operations from the iPhone lock screen.

Run from the repo root (on the droplet, on the disable-writes branch,
after merging upstream):
    python3 restrict-writes.py

Walks src/monarch_mcp_server/tools/*.py and comments out the @mcp.tool()
decorator for any async def whose name is in DISABLE. Everything else
(reads + the curated safe writes) is left registered. Function bodies are
untouched, so the change is minimal and reversible.

Policy (17 write tools total):

  ENABLED (8, reversible edits — the whole point of this change):
    categorize_transaction        change a txn's category
    bulk_categorize_transactions  same, in bulk
    update_transaction            edit fields (category/merchant/notes/date)
    update_transaction_notes      edit the notes field
    mark_transaction_reviewed     clear needs-review
    set_transaction_tags          replace a txn's tags (existing tags)
    add_transaction_tag           add an existing tag to a txn
    split_transaction             split a txn into parts

  DISABLED (9, footguns — enable individually later if wanted):
    delete_transaction            destructive
    create_transaction           creates data / duplicate risk
    create_transaction_rule       affects FUTURE transactions
    update_transaction_rule       affects FUTURE transactions
    delete_transaction_rule       affects FUTURE transactions
    set_budget_amount             budget config (deferred by choice)
    create_transaction_category   mutates the category taxonomy
    create_transaction_tag        mutates the tag taxonomy
    upload_account_balance_history bulk account-data write
"""

import re
from pathlib import Path

# Higher-blast-radius write tools to keep UNREGISTERED. Everything not in
# this set (reads + the 8 curated safe writes) stays live.
DISABLE = {
    # transactions
    "delete_transaction",
    "create_transaction",
    # rules (act on future transactions)
    "create_transaction_rule",
    "update_transaction_rule",
    "delete_transaction_rule",
    # budgets
    "set_budget_amount",
    # taxonomy creation / mutation
    "create_transaction_category",
    "create_transaction_tag",
    "update_category",  # renaming/regrouping ripples across every txn in it
    # merchants (added upstream in robcerda PR #73 sync; new blast radius)
    "update_merchant",  # can ripple into recurring detection + auto-categorization
    "review_recurring_stream",  # approves/edits recurring streams -> future-affecting
    # accounts
    "upload_account_balance_history",
    # NOTE: refresh_accounts is intentionally LEFT ENABLED — it only asks
    # Monarch to pull fresh institution data (no mutation of Aaron's
    # records) and supports the "no stale balances" rule.
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
                if m and m.group(1) in DISABLE:
                    indent = re.match(r"^(\s*)", line).group(1)
                    new_lines.append(
                        f"{indent}# @mcp.tool()  # disabled: footgun write op"
                    )
                    disabled.append(f"{py_file.name}:{i+1} -> {m.group(1)}")
                    i += 1
                    continue
        new_lines.append(line)
        i += 1
    py_file.write_text("\n".join(new_lines))

def scan_state() -> tuple[set[str], set[str]]:
    """Re-scan tools/*.py fresh. Return (all_defs, registered_defs) where
    registered = async defs whose nearest preceding decorator is an ACTIVE
    (uncommented) @mcp.tool()."""
    all_defs: set[str] = set()
    registered: set[str] = set()
    for pf in sorted(TOOLS_DIR.glob("*.py")):
        ls = pf.read_text().split("\n")
        for k, ln in enumerate(ls):
            dm = re.match(r"^\s*async def (\w+)\s*\(", ln)
            if not dm:
                continue
            all_defs.add(dm.group(1))
            # look upward past blanks/decorators for an active @mcp.tool()
            p = k - 1
            while p >= 0 and (ls[p].strip() == "" or ls[p].strip().startswith("@")):
                if re.match(r"^\s*@mcp\.tool\(\)\s*$", ls[p]):
                    registered.add(dm.group(1))
                    break
                p -= 1
    return all_defs, registered


all_defs, registered = scan_state()
still_on = sorted(DISABLE & registered)          # should be empty
absent = sorted(DISABLE - all_defs)              # renamed/removed upstream
off = sorted(DISABLE - registered - set(absent))  # correctly disabled

print(f"Disabled {len(disabled)} footgun write-tool registration(s) this run:")
for d in disabled:
    print(f"  {d}")
print(f"Footguns OFF: {len(off)}/{len(DISABLE)} — {', '.join(off)}")

if still_on:
    print(f"\n⚠  STILL REGISTERED (disable failed): {', '.join(still_on)}. "
          "Do NOT restart until resolved.")
if absent:
    print(f"\n⚠  {len(absent)} name(s) in DISABLE not found in source (renamed "
          f"upstream?): {', '.join(absent)}. Verify against tools/*.py.")
if not still_on and not absent:
    print("✅ All footguns disabled; curated safe-write set is live.")

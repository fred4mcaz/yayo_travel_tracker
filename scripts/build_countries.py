#!/usr/bin/env python3
"""Generate backend/app/countries.py from the frontend country list.

    python scripts/build_countries.py

The frontend list is the source of truth for display names. Deriving the Python
side from it means the API and the UI cannot drift into calling the same country
two different things.
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE = ROOT / "frontend" / "src" / "lib" / "countries.ts"
TARGET = ROOT / "backend" / "app" / "countries.py"

ENTRY = re.compile(r'\{\s*code:\s*"([A-Z]{2})",\s*name:\s*"((?:[^"\\]|\\.)*)"\s*\}')


def main() -> int:
    if not SOURCE.is_file():
        print(f"error: {SOURCE} not found", file=sys.stderr)
        return 1

    pairs = [
        (code, name.replace('\\"', '"'))
        for code, name in ENTRY.findall(SOURCE.read_text(encoding="utf-8"))
    ]
    if len(pairs) < 240:
        print(f"error: only parsed {len(pairs)} countries, expected ~249", file=sys.stderr)
        return 1

    lines = [
        '"""ISO 3166-1 alpha-2 country names.',
        "",
        "Generated from frontend/src/lib/countries.ts by scripts/build_countries.py.",
        "Do not edit by hand -- edit the frontend list and regenerate, so the API and",
        "the UI can never disagree about what a country is called.",
        '"""',
        "",
        "COUNTRY_NAMES: dict[str, str] = {",
    ]
    lines += [f'    "{code}": "{name}",' for code, name in sorted(pairs)]
    lines += [
        "}",
        "",
        "",
        "def country_name(code: str) -> str:",
        '    """Display name, falling back to the code itself for anything unknown."""',
        "    if not code:",
        '        return ""',
        "    return COUNTRY_NAMES.get(code.upper(), code.upper())",
        "",
    ]
    TARGET.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {TARGET.relative_to(ROOT)} with {len(pairs)} countries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

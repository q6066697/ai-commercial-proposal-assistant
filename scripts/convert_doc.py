"""
One-off conversion of legacy Word .doc files to .docx via Microsoft Word.

The tender pipeline itself reads only .docx / .pdf / .txt and does not depend
on Windows or Word; run this once for any .doc that arrives with a tender:

    python scripts/convert_doc.py data/tenders/raw/krasnodar_bank
    python scripts/convert_doc.py path/to/file.doc [--force]

Each X.doc is saved as X.docx next to it (tables are kept, unlike a plain
text export). Requires Windows with Word installed; it is driven through
PowerShell COM automation, so no extra Python packages are needed.

The documents come from third parties, so Word opens them read-only, without
adding them to the recent-files list, and with macros force-disabled
(AutomationSecurity = msoAutomationSecurityForceDisable).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

WD_FORMAT_XML_DOCUMENT = 16

POWERSHELL = r"""
param([string]$src, [string]$dst)
$ErrorActionPreference = 'Stop'
$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
$word.AutomationSecurity = 3
try {
    $doc = $word.Documents.Open($src, $false, $true, $false)
    $doc.SaveAs2($dst, %d)
    $doc.Close(0)
} finally {
    $word.Quit()
}
""" % WD_FORMAT_XML_DOCUMENT


def convert(src: Path, force: bool = False) -> Path | None:
    dst = src.with_suffix(".docx")
    if dst.exists() and not force:
        print(f"skip  {src} (already converted: {dst.name}, use --force to redo)")
        return None
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", f"& {{{POWERSHELL}}} -src '{src.resolve()}' -dst '{dst.resolve()}'"],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not dst.exists():
        raise RuntimeError(f"Word could not convert {src}:\n{result.stderr.strip()}")
    print(f"ok    {src} -> {dst.name}")
    return dst


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert .doc files to .docx via Microsoft Word")
    parser.add_argument("paths", nargs="+", help=".doc files or folders containing them")
    parser.add_argument("--force", action="store_true", help="overwrite existing .docx")
    args = parser.parse_args()

    if sys.platform != "win32":
        sys.exit("convert_doc.py needs Windows with Microsoft Word; "
                 "alternatively re-save the file as .docx by hand or use "
                 "`soffice --headless --convert-to docx`.")

    files: list[Path] = []
    for p in map(Path, args.paths):
        files.extend(sorted(p.glob("*.doc")) if p.is_dir() else [p])
    if not files:
        sys.exit("no .doc files found")
    for f in files:
        convert(f, force=args.force)


if __name__ == "__main__":
    main()

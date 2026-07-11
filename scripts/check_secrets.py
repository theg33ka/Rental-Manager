from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path


COMPROMISED_PIN_DIGESTS = {
    "d59a23c3feff6c21bbd651244d14c5639d3aa704751d4ce7aaa481712a18456d",
    "cbfad02f9ed2a8d1e08d8f74f5303e9eb93637d47f82ab6f1c15871cf8dd0481",
}
SECRET_PATTERNS = {
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "telegram-token": re.compile(r"\b\d{7,12}:[A-Za-z0-9_-]{30,}\b"),
    "github-token": re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{30,}\b"),
    "aws-access-key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
}
TEXT_SUFFIXES = {".py", ".js", ".html", ".java", ".xml", ".md", ".txt", ".yml", ".yaml", ".json", ".toml", ".ini", ".sh", ".ps1"}


def tracked_files() -> list[Path]:
    result = subprocess.run(["git", "ls-files", "-z"], check=True, capture_output=True)
    return [Path(item.decode("utf-8")) for item in result.stdout.split(b"\0") if item]


def main() -> None:
    findings: list[str] = []
    for path in tracked_files():
        if path.suffix.lower() not in TEXT_SUFFIXES or not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(lines, 1):
            for category, pattern in SECRET_PATTERNS.items():
                if pattern.search(line):
                    findings.append(f"{path.as_posix()}:{line_number} {category}")
            for candidate in re.findall(r"(?<!\d)\d{4,12}(?!\d)", line):
                if hashlib.sha256(candidate.encode("utf-8")).hexdigest() in COMPROMISED_PIN_DIGESTS:
                    findings.append(f"{path.as_posix()}:{line_number} compromised-pin")
    if findings:
        raise SystemExit("Potential secrets found:\n" + "\n".join(sorted(set(findings))))
    print("secret scan ok")


if __name__ == "__main__":
    main()

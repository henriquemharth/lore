"""Privacy: denied paths, sensitive documents and redaction of secrets in outgoing text.

- ``denied``: the file is never read nor returned by anyone (scanner and MCP).
- ``sensitive``: the document stays in the local index, but the MCP server never hands out anything from it.
- ``redact``: replaces secret-looking values with ``[REDACTED: kind]`` before text leaves through MCP or
  goes into the audit log. It does not change the index nor the .md files.
"""

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

from lore.sources import ConfigError, match

DEFAULT_DENY = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.keystore", "id_rsa*", "id_ed25519*",
    "*secret*", "*credential*",
)

_SECRETS: tuple[tuple[str, re.Pattern[str], int], ...] = (
    # (label, pattern, group that is replaced; 0 = everything)
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S), 0),
    ("AWS key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), 0),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), 0),
    ("Anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"), 0),
    ("API key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), 0),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), 0),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), 0),
    (
        "secret value",
        re.compile(
            # SENHA/SEGREDO: Portuguese for password/secret, common in variable names.
            r"\b[A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API_KEY|APIKEY|PRIVATE_KEY|SENHA|SEGREDO)[A-Z0-9_]*"
            r"\s*=\s*[\"']?(?![<$*{\[]|\.\.\.)([^\s\"'`]{6,})"
        ),
        1,
    ),
    (
        "secret value",
        re.compile(
            r"(?im)^\s*[\"']?(?:password|passwd|secret|token|api_key|apikey|senha|segredo)[\"']?\s*[:=]\s*[\"']?"
            r"(?![<$*{\[]|\.\.\.)([^\s\"'`]{6,})"
        ),
        1,
    ),
    ("password in URL", re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:([^\s@/]{3,})@"), 1),
)


@dataclass(frozen=True)
class SensitiveRule:
    sources: tuple[str, ...]
    paths: tuple[str, ...]
    reason: str

    def matches(self, source_id: str, path: str) -> bool:
        if self.sources and source_id not in self.sources:
            return False
        return any(match(path, p) for p in self.paths)


@dataclass(frozen=True)
class Privacy:
    deny: tuple[str, ...] = DEFAULT_DENY
    sensitive: tuple[SensitiveRule, ...] = ()

    def denied(self, path: str) -> bool:
        name = PurePosixPath(path).name
        return any(match(name, p) or match(path, p) or match(path, f"**/{p}") for p in self.deny)

    def sensitive_reason(self, source_id: str, path: str) -> str | None:
        if self.denied(path):
            return "path is on the deny list of privacy.yaml"
        rule = next((r for r in self.sensitive if r.matches(source_id, path)), None)
        return rule.reason if rule else None

    def blocked(self, source_id: str, path: str) -> bool:
        return self.sensitive_reason(source_id, path) is not None


def load_privacy(file: Path | None) -> Privacy:
    if file is None or not Path(file).exists():
        return Privacy()
    try:
        data = yaml.safe_load(Path(file).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as error:
        raise ConfigError(f"Could not read {file}: {error}") from error
    if not isinstance(data, dict):
        raise ConfigError(f"{file}: expected a mapping.")
    deny = tuple(str(p) for p in data.get("deny") or [])
    # The default list always applies; the file only adds to it.
    deny = tuple(dict.fromkeys((*DEFAULT_DENY, *deny)))
    sensitive = []
    for index, r in enumerate(data.get("sensitive") or [], start=1):
        reason = str(r.get("reason") or "").strip()
        paths = tuple(str(c) for c in r.get("paths") or [])
        if not reason or not paths:
            raise ConfigError(f"privacy.yaml: sensitive rule {index} needs 'paths' and 'reason'.")
        sensitive.append(SensitiveRule(tuple(str(s) for s in r.get("sources") or []), paths, reason))
    return Privacy(deny=deny, sensitive=tuple(sensitive))


def redact(text: str | None) -> tuple[str, int]:
    """Replaces secrets with markers. Returns (text, number of redactions)."""
    if not text:
        return text or "", 0
    total = 0
    for label, pattern, group in _SECRETS:
        def replace(found: re.Match[str], label: str = label, group: int = group) -> str:
            nonlocal total
            total += 1
            if group == 0:
                return f"[REDACTED: {label}]"
            start, end = found.span(group)
            base = found.start()
            whole = found.group(0)
            return whole[: start - base] + f"[REDACTED: {label}]" + whole[end - base :]

        text = pattern.sub(replace, text)
    return text, total

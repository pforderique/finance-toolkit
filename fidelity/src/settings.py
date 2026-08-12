"""TOML-backed settings for the Fidelity portfolio sync tool.

Settings replace the old hardcoded `ACCOUNT_NAME_TO_SHEET_LABEL` map and env-var
spreadsheet id. See fidelity/settings.toml for the on-disk shape.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import tomllib

try:
    import tomli_w
except ImportError as exc:  # pragma: no cover - dependency guard
    raise ImportError(
        "tomli-w is required to write settings.toml. Install it with "
        "`uv pip install tomli-w` (or add it to fidelity/pyproject.toml deps)."
    ) from exc


DEFAULT_SETTINGS_PATH = Path(__file__).resolve().parent.parent / "settings.toml"


class SettingsError(Exception):
    """Raised when settings.toml is missing, malformed, or fails validation."""


@dataclass
class AccountMapping:
    """One [[accounts]] block: a Fidelity account mapped to a sheet dropdown label."""

    number: str
    name: str
    label: str
    enabled: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "number": self.number,
            "name": self.name,
            "label": self.label,
            "enabled": self.enabled,
        }


@dataclass
class SheetSettings:
    spreadsheet_id: str
    tab: str = "Portfolio"
    table: str = "INVESTMENT_HOLDINGS"


@dataclass
class SymbolSettings:
    ignore_prefixes: List[str] = field(default_factory=lambda: ["SPAXX", "FDRXX"])
    ignore_exact: List[str] = field(default_factory=lambda: ["PENDING ACTIVITY"])
    aliases: Dict[str, str] = field(default_factory=lambda: {"BRKB": "BRK.B"})


@dataclass
class ToleranceSettings:
    shares: float = 1e-6
    avg_cost: float = 0.005


@dataclass
class Settings:
    """Fully parsed & validated settings.toml."""

    sheet: SheetSettings
    symbols: SymbolSettings
    tolerance: ToleranceSettings
    accounts: List[AccountMapping]
    path: Path

    # -- lookups -----------------------------------------------------
    def find_by_number(self, number: str) -> Optional[AccountMapping]:
        for acct in self.accounts:
            if acct.number == number:
                return acct
        return None

    def find_by_name(self, name: str) -> Optional[AccountMapping]:
        lowered = name.strip().lower()
        for acct in self.accounts:
            if acct.name.strip().lower() == lowered:
                return acct
        return None

    def resolve_label(self, number: str, name: str) -> Optional[str]:
        """Resolve a sheet label for a CSV row's account number/name.

        Match precedence: number first, then case-insensitive name.
        Only enabled accounts resolve to a label.
        """
        acct = self.find_by_number(number) if number else None
        if acct is None and name:
            acct = self.find_by_name(name)
        if acct is None or not acct.enabled:
            return None
        return acct.label

    def owned_labels(self) -> set:
        """Sheet labels for all enabled accounts. Replaces `_is_fidelity_row`."""
        return {acct.label for acct in self.accounts if acct.enabled}

    # -- mutation (in-memory; caller persists via save_settings) -----
    def add_account(self, acct: AccountMapping) -> None:
        if self.find_by_number(acct.number) is not None:
            raise SettingsError(
                f"Account number '{acct.number}' already exists in {self.path}"
            )
        self.accounts.append(acct)

    def remove_account(self, number: str) -> AccountMapping:
        acct = self.find_by_number(number)
        if acct is None:
            raise SettingsError(f"No account with number '{number}' in {self.path}")
        self.accounts.remove(acct)
        return acct

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sheet": {
                "spreadsheet_id": self.sheet.spreadsheet_id,
                "tab": self.sheet.tab,
                "table": self.sheet.table,
            },
            "symbols": {
                "ignore_prefixes": list(self.symbols.ignore_prefixes),
                "ignore_exact": list(self.symbols.ignore_exact),
                "aliases": dict(self.symbols.aliases),
            },
            "tolerance": {
                "shares": self.tolerance.shares,
                "avg_cost": self.tolerance.avg_cost,
            },
            "accounts": [acct.to_dict() for acct in self.accounts],
        }


def _require(mapping: Dict[str, Any], key: str, path: Path, section: str) -> Any:
    if key not in mapping:
        raise SettingsError(f"{path}: missing required key '{key}' in [{section}]")
    return mapping[key]


def _validate(settings: Settings) -> None:
    seen_numbers: Dict[str, str] = {}
    enabled_count = 0
    for acct in settings.accounts:
        if not acct.number or not str(acct.number).strip():
            raise SettingsError(f"{settings.path}: account has an empty 'number'")
        if not acct.label or not acct.label.strip():
            raise SettingsError(
                f"{settings.path}: account '{acct.number}' has an empty 'label'"
            )
        if acct.number in seen_numbers:
            raise SettingsError(
                f"{settings.path}: duplicate account number '{acct.number}'"
            )
        seen_numbers[acct.number] = acct.name
        if acct.enabled:
            enabled_count += 1

    if enabled_count < 1:
        raise SettingsError(
            f"{settings.path}: at least one account must be enabled"
        )

    if not settings.sheet.spreadsheet_id:
        raise SettingsError(f"{settings.path}: [sheet].spreadsheet_id is required")


def load_settings(path: Optional[Path] = None) -> Settings:
    """Load and validate settings.toml. Raises SettingsError on any problem."""

    settings_path = Path(path) if path is not None else DEFAULT_SETTINGS_PATH

    if not settings_path.exists():
        raise SettingsError(f"Settings file not found: {settings_path}")

    try:
        raw_bytes = settings_path.read_bytes()
        data = tomllib.loads(raw_bytes.decode("utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise SettingsError(f"{settings_path}: malformed TOML ({exc})") from exc
    except OSError as exc:
        raise SettingsError(f"{settings_path}: could not read file ({exc})") from exc

    sheet_raw = data.get("sheet", {})
    if not isinstance(sheet_raw, dict):
        raise SettingsError(f"{settings_path}: [sheet] must be a table")
    sheet = SheetSettings(
        spreadsheet_id=_require(sheet_raw, "spreadsheet_id", settings_path, "sheet"),
        tab=sheet_raw.get("tab", "Portfolio"),
        table=sheet_raw.get("table", "INVESTMENT_HOLDINGS"),
    )

    symbols_raw = data.get("symbols", {})
    if not isinstance(symbols_raw, dict):
        raise SettingsError(f"{settings_path}: [symbols] must be a table")
    symbols = SymbolSettings(
        ignore_prefixes=list(symbols_raw.get("ignore_prefixes", ["SPAXX", "FDRXX"])),
        ignore_exact=list(symbols_raw.get("ignore_exact", ["PENDING ACTIVITY"])),
        aliases=dict(symbols_raw.get("aliases", {"BRKB": "BRK.B"})),
    )

    tolerance_raw = data.get("tolerance", {})
    if not isinstance(tolerance_raw, dict):
        raise SettingsError(f"{settings_path}: [tolerance] must be a table")
    tolerance = ToleranceSettings(
        shares=float(tolerance_raw.get("shares", 1e-6)),
        avg_cost=float(tolerance_raw.get("avg_cost", 0.005)),
    )

    accounts_raw = data.get("accounts", [])
    if not isinstance(accounts_raw, list):
        raise SettingsError(f"{settings_path}: [[accounts]] must be an array of tables")

    accounts: List[AccountMapping] = []
    for idx, entry in enumerate(accounts_raw):
        if not isinstance(entry, dict):
            raise SettingsError(
                f"{settings_path}: accounts[{idx}] must be a table, got {type(entry).__name__}"
            )
        try:
            accounts.append(
                AccountMapping(
                    number=str(_require(entry, "number", settings_path, "accounts")),
                    name=str(_require(entry, "name", settings_path, "accounts")),
                    label=str(_require(entry, "label", settings_path, "accounts")),
                    enabled=bool(entry.get("enabled", True)),
                )
            )
        except SettingsError:
            raise
        except (TypeError, ValueError) as exc:
            raise SettingsError(f"{settings_path}: accounts[{idx}] is malformed ({exc})") from exc

    settings = Settings(
        sheet=sheet,
        symbols=symbols,
        tolerance=tolerance,
        accounts=accounts,
        path=settings_path,
    )
    _validate(settings)
    return settings


def save_settings(settings: Settings, path: Optional[Path] = None) -> None:
    """Atomically write settings back to disk (temp file + os.replace)."""

    _validate(settings)
    target = Path(path) if path is not None else settings.path
    target.parent.mkdir(parents=True, exist_ok=True)

    payload = tomli_w.dumps(settings.to_dict())

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

from __future__ import annotations

import json
import os
import re
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)),
    ("phone", re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?(?:\d{3}[\s.-]?){2}\d{4}(?!\d)")),
    ("reservation", re.compile(r"\b(?=[A-Z0-9]*\d)[A-Z0-9]{6}\b")),
    ("flight", re.compile(r"\bHAT\d{3}\b")),
    ("order", re.compile(r"#W\d{7}")),
    ("item", re.compile(r"\b\d{10}\b")),
    (
        "customer",
        re.compile(r"\b(?:c|cus|customer)[-_]?\d{4,}\b", re.IGNORECASE),
    ),
    ("line", re.compile(r"\b(?:l|line|ln)[-_]?\d{4,}\b", re.IGNORECASE)),
    ("user", re.compile(r"\b[a-z]+_[a-z]+_\d{4}\b", re.IGNORECASE)),
    ("date", re.compile(r"\b\d{4}-\d{2}-\d{2}\b")),
    ("amount", re.compile(r"(?:[$€£]\d+(?:\.\d{2})?|\b\d+\.\d{2})\b")),
    (
        "payment",
        re.compile(r"\b(?:credit_card|gift_card|paypal|certificate)_\d+\b", re.IGNORECASE),
    ),
)

_PLACEHOLDER = re.compile(r"(<[A-Z][A-Z0-9_]*>)")
_IDENTIFIER_FIELD = re.compile(r"(?:^id$|_id$|_uuid$|_code$|_number$|_reference$)")
# tau2 labels for expected actions. Some labels are tool names, which are not task data.
_BENCHMARK_LABEL_FIELDS = frozenset({"action_id"})


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return {}


def _identifier_pattern(raw: str) -> re.Pattern[str]:
    flags = 0 if len(raw) == 2 and raw.isalpha() and raw.isupper() else re.IGNORECASE
    return re.compile(rf"(?<!\w){re.escape(raw)}(?!\w)", flags)


@dataclass
class Redactor:
    replacements: dict[str, str] = field(default_factory=dict)
    replacement_map: dict[str, str] = field(default_factory=dict)
    _counters: defaultdict[str, int] = field(default_factory=lambda: defaultdict(int))
    _raw_to_placeholder: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_values(cls, values: dict[str, Any]) -> Redactor:
        redactor = cls()
        for key, raw in values.items():
            if isinstance(raw, str) and len(raw) >= 3:
                redactor._add(f"<{key.upper()}>", raw)
        redactor._seed_strings(values)
        return redactor

    @classmethod
    def from_task(cls, task: Any, domain: str) -> Redactor:
        redactor = cls()
        task_data = _as_dict(task)
        scenario = _as_dict(task_data.get("user_scenario"))
        redactor._seed_strings(scenario.get("persona"))
        redactor._seed_strings(scenario.get("instructions"))
        redactor._seed_strings(task_data)
        redactor._seed_domain_users(domain, scenario)
        return redactor

    def _seed_domain_users(self, domain: str, scenario: dict[str, Any]) -> None:
        data_root = Path(
            os.environ.get("TAU2_DATA_DIR")
            or Path(__file__).resolve().parents[3] / "tau2-bench" / "data"
        )
        db_dir = data_root / "tau2" / "domains" / domain
        db_path = db_dir / ("db.toml" if domain == "telecom" else "db.json")
        if not db_path.is_file():
            return
        with db_path.open("rb" if db_path.suffix == ".toml" else "r") as handle:
            database = tomllib.load(handle) if db_path.suffix == ".toml" else json.load(handle)
        if domain in {"airline", "retail"}:
            users = database.get("users", {})
            for user_id, user in users.items():
                if self._record_matches_scenario(user_id, user, scenario):
                    self._seed_record(user)
                    return
        elif domain == "telecom":
            customers = database.get("customers", [])
            lines = database.get("lines", [])
            selected_customer: dict[str, Any] | None = None
            for customer in customers:
                if self._record_matches_scenario("", customer, scenario):
                    selected_customer = customer
                    break
            if selected_customer is None:
                for line in lines:
                    if self._record_matches_scenario("", line, scenario):
                        line_id = str(line.get("line_id") or "")
                        selected_customer = next(
                            (
                                customer
                                for customer in customers
                                if line_id in customer.get("line_ids", [])
                            ),
                            None,
                        )
                        if selected_customer is not None:
                            break
            if selected_customer is not None:
                self._seed_record(selected_customer)
                line_ids = set(selected_customer.get("line_ids", []))
                for line in lines:
                    if line.get("line_id") in line_ids:
                        self._seed_record(line)
        elif domain == "banking_knowledge":
            users = _as_dict(database.get("users")).get("data", {})
            for user_id, user in users.items():
                if not self._record_matches_scenario(user_id, user, scenario):
                    continue
                self._seed_record(user)
                self._add("<FULL_NAME>", _as_dict(user).get("name"))
                for table in database.values():
                    for record in _as_dict(_as_dict(table).get("data")).values():
                        if _as_dict(record).get("user_id") == user_id:
                            self._seed_record(record)
                return

    def _record_matches_scenario(
        self, record_id: str, record: Any, scenario: dict[str, Any]
    ) -> bool:
        scenario_text = json.dumps(scenario, default=str)
        values = [record_id]
        record_data = _as_dict(record)
        for key in (
            "user_id",
            "customer_id",
            "email",
            "phone",
            "phone_number",
            "full_name",
            "name",
            "line_id",
        ):
            value = record_data.get(key)
            if isinstance(value, dict):
                values.extend(str(nested) for nested in value.values())
            elif value is not None:
                values.append(str(value))
        for key in ("reservations", "orders", "line_ids"):
            values.extend(str(value) for value in record_data.get(key, []))
        return any(
            value
            and len(value) >= 4
            and re.search(rf"(?<!\w){re.escape(value)}(?!\w)", scenario_text, re.IGNORECASE)
            for value in values
        )

    def _seed_record(self, record: Any) -> None:
        record_data = _as_dict(record)
        self._seed_strings(record_data)
        for key, placeholder in (
            ("user_id", "<USER_ID>"),
            ("customer_id", "<CUSTOMER_ID>"),
            ("line_id", "<LINE_ID>"),
            ("first_name", "<FIRST_NAME>"),
            ("last_name", "<LAST_NAME>"),
            ("full_name", "<FULL_NAME>"),
            ("date_of_birth", "<DATE_OF_BIRTH>"),
            ("dob", "<DATE_OF_BIRTH>"),
            ("email", "<EMAIL>"),
            ("phone", "<PHONE>"),
            ("phone_number", "<PHONE>"),
        ):
            self._add(placeholder, record_data.get(key))
        name = _as_dict(record_data.get("name"))
        self._add("<FIRST_NAME>", name.get("first") or name.get("first_name"))
        self._add("<LAST_NAME>", name.get("last") or name.get("last_name"))
        address = _as_dict(record_data.get("address"))
        for field_name, value in address.items():
            self._add(f"<ADDRESS_{field_name.upper()}>", value)
        payment_methods = record_data.get("payment_methods", {})
        if isinstance(payment_methods, dict):
            payment_values = payment_methods.keys()
        else:
            payment_values = payment_methods
        for index, raw in enumerate(payment_values, start=1):
            self._add(f"<PAYMENT_METHOD_{index}>", raw)
        for category in ("reservations", "orders", "line_ids", "bill_ids"):
            for index, value in enumerate(record_data.get(category, []), start=1):
                self._add(f"<{category[:-1].upper()}_{index}>", value)

    def _seed_strings(self, value: Any) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                self._seed_strings(nested)
                if (
                    isinstance(nested, str)
                    and isinstance(key, str)
                    and len(nested) >= 4
                    and not any(character.isspace() for character in nested)
                    and _IDENTIFIER_FIELD.search(key)
                    and key not in _BENCHMARK_LABEL_FIELDS
                ):
                    self._add("<TASK_IDENTIFIER>", nested)
        elif isinstance(value, list):
            for nested in value:
                self._seed_strings(nested)
        elif isinstance(value, str):
            for category, pattern in _PATTERNS:
                for match in pattern.finditer(value):
                    if category == "reservation" and match.group(0).startswith("HAT"):
                        continue
                    self._placeholder_for_pattern(category, match.group(0))

    def _add(self, placeholder: str, raw: Any) -> str:
        if raw is None or raw == "":
            return placeholder
        value = str(raw)
        key = value.casefold()
        if key in self._raw_to_placeholder:
            return self._raw_to_placeholder[key]
        original = placeholder
        index = 2
        while placeholder in self.replacements:
            placeholder = f"{original[:-1]}_{index}>"
            index += 1
        self.replacements[placeholder] = value
        self.replacement_map[placeholder] = value
        self._raw_to_placeholder[key] = placeholder
        return placeholder

    def _add_indexed(self, category: str, raw: Any) -> None:
        if raw is None or raw == "":
            return
        key = str(raw).casefold()
        if key in self._raw_to_placeholder:
            return
        self._counters[category] += 1
        self._add(f"{category[:-1]}_{self._counters[category]}>", raw)

    def _placeholder_for_pattern(self, category: str, raw: str) -> str | None:
        existing = self._raw_to_placeholder.get(raw.casefold())
        if existing:
            return existing
        if category == "email":
            placeholder = "<EMAIL>"
        elif category == "phone":
            placeholder = "<PHONE>"
        elif category == "date":
            placeholder = "<DATE>"
        elif category == "amount":
            placeholder = "<AMOUNT>"
        elif category == "flight":
            placeholder = "<FLIGHT_NUMBER>"
        elif category == "user":
            placeholder = "<USER_ID>"
        elif category == "payment":
            self._counters["<PAYMENT_METHOD>"] += 1
            placeholder = f"<PAYMENT_METHOD_{self._counters['<PAYMENT_METHOD>']}>"
        elif category == "reservation":
            self._counters["<RESERVATION>"] += 1
            placeholder = f"<RESERVATION_{self._counters['<RESERVATION>']}>"
        elif category == "order":
            self._counters["<ORDER>"] += 1
            placeholder = f"<ORDER_{self._counters['<ORDER>']}>"
        else:
            self._counters[category] += 1
            placeholder = f"<{category.upper()}_{self._counters[category]}>"
        return self._add(placeholder, raw)

    def redact(self, text: str) -> str:
        result = str(text)
        for placeholder, raw in sorted(
            self.replacements.items(), key=lambda item: len(item[1]), reverse=True
        ):
            pattern = _identifier_pattern(raw)
            result = "".join(
                part if index % 2 else pattern.sub(placeholder, part)
                for index, part in enumerate(_PLACEHOLDER.split(result))
            )
        for category, pattern in _PATTERNS:

            def replace(match: re.Match[str], category: str = category) -> str:
                raw = match.group(0)
                if category == "reservation" and raw.startswith("HAT"):
                    return raw
                placeholder = self._placeholder_for_pattern(category, raw)
                return placeholder or raw

            result = "".join(
                part if index % 2 else pattern.sub(replace, part)
                for index, part in enumerate(_PLACEHOLDER.split(result))
            )
        return result

    def audit(self, text: str, maps: list[dict[str, str]]) -> list[str]:
        hits: list[str] = []
        for mapping in maps:
            for raw in mapping.values():
                if not raw or raw in hits:
                    continue
                if _identifier_pattern(raw).search(text):
                    hits.append(raw)
        return hits

    def audit_maps(self) -> list[dict[str, str]]:
        return [self.replacement_map.copy()]

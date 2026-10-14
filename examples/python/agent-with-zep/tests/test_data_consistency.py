"""Dataset consistency: records and report front matter match facts.md."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

DATA = Path(__file__).resolve().parent.parent / "data"


def _records(name):
    return json.loads((DATA / "records" / f"{name}.json").read_text())


def test_eu_valid_filings():
    filings = _records("regulatory_filings")
    eu_valid = {f["product"] for f in filings if f["region"] == "EU" and f["status"] == "valid"}
    assert eu_valid == {"Aster 410", "Aster 520", "Lyric 300"}


def test_open_high_severity_issues():
    issues = _records("quality_issues")
    open_high = {
        i["issue_id"] for i in issues if i["severity"] == "high" and i["status"].startswith("open")
    }
    assert open_high == {"QI-1041", "QI-1068"}


def test_reliability_aster410_intersection():
    employees = _records("employees")
    names = {
        e["name"]
        for e in employees
        if e["team"] == "Reliability Engineering" and "Aster 410" in e["works_on"]
    }
    assert names == {"Lena Vogt", "Owen Briggs"}


def test_report_front_matter():
    required = {"report_id", "report_type", "product", "date", "author"}
    reports = sorted((DATA / "reports").glob("R*.md"))
    assert len(reports) == 20
    for path in reports:
        meta = yaml.safe_load(path.read_text().split("---", 2)[1])
        assert required <= set(meta), path.name
        assert meta["report_id"] == path.stem
        # the body must state the date and author too
        body = path.read_text().split("---", 2)[2]
        assert str(meta["date"]) in body, path.name
        assert meta["author"] in body, path.name


def test_customers_reporting_aster410():
    customers = _records("customers")
    reporters = {c["name"] for c in customers if "Aster 410" in c.get("notes", "")}
    assert reporters == {"Northgate Regional Hospital", "St. Brigid's Hospital"}

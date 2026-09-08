"""UI boundary: validate uploaded CSVs, then call the reviewed engine unchanged."""
from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from src.engine import Dataset, IssueSeverity, load_rules, run

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = ("cases.csv", "transactions.csv", "parties.csv", "transaction_parties.csv")
OPTIONAL = "transaction_links.csv"


def sample_files():
    return {name: (ROOT / "data/sample" / name).read_bytes()
            for name in (*REQUIRED, OPTIONAL)}


def fingerprint(files):
    digest = hashlib.sha256()
    for name, content in sorted(files.items()):
        digest.update(name.encode() + b"\0" + len(content).to_bytes(8, "big") + content)
    return digest.hexdigest()


def analyze(files):
    missing = set(REQUIRED) - files.keys()
    if missing:
        raise ValueError("필수 파일 누락: " + ", ".join(sorted(missing)))
    if set(files) - {*REQUIRED, OPTIONAL}:
        raise ValueError("입력 파일 이름을 확인해 주세요.")
    rules = load_rules(ROOT)
    # Dataset.load's existing directory contract is retained. No uploaded path is used.
    with TemporaryDirectory(prefix="trustee-fds-") as temporary:
        root = Path(temporary)
        target = root / "data/sample"
        target.mkdir(parents=True)
        for name, content in files.items():
            text = content.decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
            headers = reader.fieldnames or []
            template = (ROOT / "data/sample" / name).read_text(encoding="utf-8").splitlines()[0]
            expected = next(csv.reader([template]))
            if len(headers) != len(set(headers)) or not set(expected).issubset(headers):
                raise ValueError(f"{name}: 샘플의 열 이름을 모두 유지해 주세요.")
            rows = list(reader)
            if any(None in row or any(v is None for v in row.values()) for row in rows):
                raise ValueError(f"{name}: 열 개수가 맞지 않는 행이 있습니다.")
            if name in ("cases.csv", "transactions.csv") and not rows:
                raise ValueError(f"{name}: 데이터가 비어 있습니다.")
            (target / name).write_text(text, encoding="utf-8")
        ds = Dataset.load(root, unilateral_acts=rules["art391-4-gratuitous"]["action_filter"].get("unilateral_acts", []))
    blocked = any(i.severity == IssueSeverity.CRITICAL for i in ds.integrity_issues)
    results = [] if blocked else run(ds, rules)
    return ds, rules, results, blocked


def export_json(ds, rules, results, input_hash):
    return json.dumps({
        "input_sha256": input_hash,
        "rule_status": [{"rule_id": r["rule_id"], "version": r["version"], "status": r["status"]} for r in rules.values()],
        "notice": "연구·검증용. 실제 사건에 사용하지 마세요. 순출연 추정액은 회수 기대액이 아닙니다.",
        "cases": list(ds.cases.values()),
        "transactions": ds.transactions,
        "parties": list(ds.parties.values()),
        "transaction_parties": ds.links,
        "transaction_links": ds.tx_links,
        "integrity_issues": [dict(asdict(i), severity=i.severity.value, category=i.category.value) for i in ds.integrity_issues],
        "results": [asdict(r) for r in results],
    }, ensure_ascii=False, indent=2)

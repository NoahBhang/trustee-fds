"""
입력 CSV 로딩과 데이터 무결성 검사.

engine.py 설계원칙 #4 — 무결성 검사는 Dataset 로드 시점에 수행한다.
판정 로직은 engine.py, 특수관계인 분류는 relations.py 에 있다.
"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path


# ---------------------------------------------------------------- 무결성 검사

class IssueCategory(Enum):
    """무결성 문제의 범주."""
    REFERENTIAL = "참조 무결성"
    VALUE_CONSTRAINT = "값 제약"


class IssueSeverity(Enum):
    """무결성 문제의 심각도."""
    CRITICAL = 1      # 조용히 판정 오류
    WARNING = 2       # 조용히 데이터 소실
    INFO = 3          # 진단 개선


@dataclass
class IntegrityIssue:
    """무결성 검사 결과."""
    category: IssueCategory
    severity: IssueSeverity
    message: str
    details: list = field(default_factory=list)  # 상세 항목들


# ---------------------------------------------------------------- 로딩

def _date(s):
    s = (s or "").strip()
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


def _num(s):
    s = (s or "").strip()
    try:
        value = float(s) if s else None
        return value if value is None or math.isfinite(value) else None
    except ValueError:
        return None


def _rows(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


@dataclass
class Dataset:
    cases: dict                                     # case_id → case_row
    transactions: list                              # transaction_row[]
    parties: dict                                   # (case_id, party_id) → party_row
    links: list                                     # transaction_parties row[]
    tx_links: list = field(default_factory=list)     # transaction_links row[] (자금 흐름)
    integrity_issues: list[IntegrityIssue] = field(default_factory=list)
    # 상대방 없는 단독행위 목록 (art391_4_gratuitous.yaml action_filter.unilateral_acts).
    # legal_counterparty 커버리지 검사에서 제외된다. 근거는 그 YAML 주석 참조.
    unilateral_acts: frozenset = field(default_factory=frozenset)
    # 원본 행 리스트 — dict 로 접힌 뒤에는 볼 수 없는 중복 키를 검사하려면 필요하다.
    _raw_cases: list = field(default_factory=list, repr=False)
    _raw_parties: list = field(default_factory=list, repr=False)

    @classmethod
    def load(cls, root: Path, unilateral_acts=()):
        """데이터 로드 후 즉시 무결성 검사 수행.

        unilateral_acts: 상대방 없는 단독행위 action_type 집합. 룰을 먼저 로드해
        art391_4_gratuitous.yaml 의 action_filter.unilateral_acts 를 넘겨준다.
        비워두면(기본값) 모든 거래가 legal_counterparty 커버리지 검사를 받는다.
        """
        d = root / "data" / "sample"

        # 원본 데이터 읽기
        cases_rows = _rows(d / "cases.csv")
        transactions_rows = _rows(d / "transactions.csv")
        parties_rows = _rows(d / "parties.csv")
        links_rows = _rows(d / "transaction_parties.csv")
        tx_links_path = d / "transaction_links.csv"
        tx_links_rows = _rows(tx_links_path) if tx_links_path.exists() else []

        # 복합 키로 parties 로드
        ds = cls(
            cases={r["case_id"]: r for r in cases_rows},
            transactions=transactions_rows,
            parties={(r["case_id"], r["party_id"]): r for r in parties_rows},
            links=links_rows,
            tx_links=tx_links_rows,
            unilateral_acts=frozenset(unilateral_acts),
            _raw_cases=cases_rows,
            _raw_parties=parties_rows,
        )

        # 로드 직후 무결성 검사 수행
        ds.integrity_issues = ds._check_all_integrity()
        return ds

    def parties_of(self, case_id, tx_id, role):
        """
        특정 거래의 특정 역할을 맡은 당사자들을 반환.
        case_id를 명시적으로 요구하여 크로스 검증 강제.
        """
        return [self.parties[(case_id, l["party_id"])] 
                for l in self.links
                if l["transaction_id"] == tx_id and l["role"] == role
                and (case_id, l["party_id"]) in self.parties]

    def inbound_fund_flows(self, case_id, tx_id, link_types):
        """
        이 거래를 to_transaction_id 로 하는, link_type 이 link_types 안에 드는
        링크들을 반환. link_types 는 art391_4_gratuitous.yaml
        fund_flow_analysis.eligible_link_types 에서 온다.
        case_id 를 명시적으로 요구해 교차 사건 오염을 막는다 (parties_of() 와 같은 이유).
        """
        allowed = set(link_types)
        return [l for l in self.tx_links
                if l["case_id"] == case_id
                and l["to_transaction_id"] == tx_id
                and l["link_type"] in allowed]

    def _check_all_integrity(self) -> list[IntegrityIssue]:
        """모든 무결성 검사를 수행하고 문제 목록을 반환.

        비대칭 주의: check_predicate_coverage() 는 priority_signals 의 id 와 코드
        구현이 어긋나면 UnimplementedPredicate 로 즉시 실패시켜 YAML↔코드 일치를
        강제한다. 데이터 제약(related_party.yaml 의 share_sum_mismatch 등)에는 그런
        강제 장치가 없다 — 아래 검사들이 수동으로 대응한다. YAML 에 제약을 추가하면
        여기 대응 검사도 함께 추가해야 한다.
        """
        issues = []
        issues += self._check_value_formats()
        issues += self._check_party_id_references()
        issues += self._check_legal_counterparty_coverage()
        issues += self._check_case_id_cross_validation()
        issues += self._check_transaction_id_references()
        issues += self._check_duplicate_keys()
        issues += self._check_case_id_coverage()
        issues += self._check_unreferenced_parties()
        issues += self._check_share_ratio_sum()
        issues += self._check_tx_link_references()
        issues += self._check_tx_link_temporal_order()
        issues += self._check_tx_link_self_reference()
        issues += self._check_tx_link_cycles()
        return issues

    def _check_value_formats(self) -> list[IntegrityIssue]:
        """판정에 쓰는 날짜·숫자·열거값이 조용히 결측값으로 바뀌지 않게 한다."""
        invalid = []

        def add(table, row_id, field_name, value, reason):
            invalid.append({
                "table": table,
                "row_id": row_id,
                "field": field_name,
                "value": value,
                "reason": reason,
            })

        def check_dates(table, rows, id_field, fields, required=()):
            for row in rows:
                row_id = row.get(id_field, "")
                for field_name in fields:
                    raw = (row.get(field_name) or "").strip()
                    if not raw:
                        if field_name in required:
                            add(table, row_id, field_name, raw, "필수 날짜 누락")
                        continue
                    if _date(raw) is None:
                        add(table, row_id, field_name, raw, "YYYY-MM-DD 날짜 형식 아님")

        def check_numbers(table, rows, id_field, fields, ranges=None):
            ranges = ranges or {}
            for row in rows:
                row_id = row.get(id_field, "")
                for field_name in fields:
                    raw = (row.get(field_name) or "").strip()
                    if not raw:
                        continue
                    value = _num(raw)
                    if value is None:
                        add(table, row_id, field_name, raw, "숫자 형식 아님")
                        continue
                    low, high = ranges.get(field_name, (0, None))
                    if low is not None and value < low:
                        add(table, row_id, field_name, raw, f"최솟값 {low} 미만")
                    if high is not None and value > high:
                        add(table, row_id, field_name, raw, f"최댓값 {high} 초과")

        def check_enum(table, rows, id_field, field_name, allowed, allow_blank=False):
            for row in rows:
                row_id = row.get(id_field, "")
                raw = (row.get(field_name) or "").strip()
                if not raw and allow_blank:
                    continue
                if raw not in allowed:
                    add(table, row_id, field_name, raw,
                        "허용값: " + ", ".join(sorted(allowed)))

        check_dates("cases", self._raw_cases, "case_id",
                    ("petition_date", "suspension_of_payment_date", "adjudication_date"),
                    required=("petition_date",))
        check_dates("transactions", self.transactions, "transaction_id",
                    ("transaction_date",), required=("transaction_date",))
        check_dates("parties", self._raw_parties, "party_id",
                    ("relation_valid_from", "relation_valid_to"))

        money_fields = (
            "asset_fair_value", "liability_increase_value", "liability_reduction",
            "waived_right_value", "consideration_contractual", "consideration_paid",
            "debtor_direct_benefit_value",
        )
        check_numbers("transactions", self.transactions, "transaction_id", money_fields)
        check_numbers(
            "parties", self._raw_parties, "party_id",
            ("ownership_percentage", "aggregated_ownership_percentage", "kinship_degree"),
            ranges={
                "ownership_percentage": (0, 100),
                "aggregated_ownership_percentage": (0, 100),
                "kinship_degree": (1, None),
            },
        )
        check_numbers(
            "transaction_parties", self.links, "transaction_id",
            ("share_ratio", "share_amount"),
            ranges={"share_ratio": (0, 1), "share_amount": (0, None)},
        )

        check_enum("cases", self._raw_cases, "case_id", "debtor_type",
                   {"individual", "corporation"})
        check_enum("transactions", self.transactions, "transaction_id", "payment_verified",
                   {"Y", "N"}, allow_blank=True)
        check_enum("transactions", self.transactions, "transaction_id", "benefit_realizability",
                   {"realized", "expected", "unrealized"}, allow_blank=True)
        check_enum("cases", self._raw_cases, "case_id", "suspension_date_confirmed",
                   {"Y", "N"}, allow_blank=True)
        for fact_field in (
            "post_divorce_dependency", "financial_dependency", "shared_livelihood",
            "de_facto_control", "affiliate_status_verified",
        ):
            check_enum("parties", self._raw_parties, "party_id", fact_field,
                       {"Y", "N"}, allow_blank=True)
        check_enum("transaction_parties", self.links, "transaction_id", "role",
                   {"legal_counterparty", "principal_debtor", "ultimate_beneficiary"})
        check_enum("transaction_links", self.tx_links, "link_id", "link_type",
                   {"funds_flow", "same_asset", "contract_bundle"})
        check_enum("transaction_links", self.tx_links, "link_id", "confidence",
                   {"verified", "probable", "alleged"})
        for link in self.tx_links:
            if (link.get("link_type") == "funds_flow"
                    and link.get("confidence") in {"verified", "probable"}
                    and not (link.get("evidence") or "").strip()):
                add("transaction_links", link.get("link_id", ""), "evidence", "",
                    "probable 이상 자금흐름 링크의 근거 누락")

        if not invalid:
            return []
        return [IntegrityIssue(
            category=IssueCategory.VALUE_CONSTRAINT,
            severity=IssueSeverity.CRITICAL,
            message="판정 입력의 날짜·숫자·열거값 형식 오류 — 결측으로 간주하지 않음",
            details=invalid,
        )]

    def _check_party_id_references(self) -> list[IntegrityIssue]:
        """
        1군 ① : transaction_parties의 party_id가 parties에 존재하는지 확인.
        역할별로 심각도를 구분한다.
        """
        issues = []
        missing_refs = {}  # (party_id, role) -> [(tx_id, case_id), ...]
        tx_case = {t["transaction_id"]: t["case_id"] for t in self.transactions}

        for link in self.links:
            party_id = link["party_id"]
            case_id = tx_case.get(link["transaction_id"])

            if case_id is None:
                continue  # 거래가 없으면 다른 검사에서 잡음

            # 복합 키로 존재 여부 확인
            if (case_id, party_id) not in self.parties:
                key = (party_id, link["role"])
                if key not in missing_refs:
                    missing_refs[key] = []
                missing_refs[key].append((link["transaction_id"], case_id))
        
        # 역할별로 심각도를 구분하여 이슈 생성
        for (party_id, role), refs in sorted(missing_refs.items()):
            # legal_counterparty 누락이 가장 심각
            if role == "legal_counterparty":
                severity = IssueSeverity.CRITICAL
                role_msg = "【심각】법적 상대방 누락"
            else:
                severity = IssueSeverity.WARNING
                role_msg = f"【경고】{role} 누락"
            
            details = [{"tx_id": tx_id, "case_id": case_id} for tx_id, case_id in refs]
            message = f"{party_id} — parties.csv 에 없음 ({role_msg})"
            issues.append(IntegrityIssue(
                category=IssueCategory.REFERENTIAL,
                severity=severity,
                message=message,
                details=details
            ))
        
        return issues

    def _check_legal_counterparty_coverage(self) -> list[IntegrityIssue]:
        """
        1군 ② : 모든 거래가 legal_counterparty를 적어도 하나 가져야 한다.
        없으면 조용히 6월로 판정된다 (판정 오류).

        예외: unilateral_acts (상속포기 등 상대방 없는 단독행위). legal_counterparty
        부재가 정상이며, 2단계에서 route_out 되어 소급기간 판정에 도달하지 않는다.
        근거는 art391_4_gratuitous.yaml action_filter.unilateral_acts 주석 참조.
        """
        issues = []
        missing_coverage = []

        for tx in self.transactions:
            case_id = tx["case_id"]
            tx_id = tx["transaction_id"]

            if tx.get("action_type") in self.unilateral_acts:
                continue  # 상대방 없는 단독행위 — 부재가 정상

            # 이 거래의 legal_counterparty 확인
            cps = [l for l in self.links
                   if l["transaction_id"] == tx_id and l["role"] == "legal_counterparty"]

            if not cps:
                missing_coverage.append({"tx_id": tx_id, "case_id": case_id})

        if missing_coverage:
            issues.append(IntegrityIssue(
                category=IssueCategory.REFERENTIAL,
                severity=IssueSeverity.CRITICAL,
                message="법적 상대방이 지정되지 않은 거래들",
                details=missing_coverage
            ))
        
        return issues

    def _check_case_id_cross_validation(self) -> list[IntegrityIssue]:
        """
        1군 ③ : parties 는 있는데 이 사건에 속하지 않은 당사자를 거래가 참조.
        다른 사건의 당사자가 이 사건에 끼어들 수 없도록.

        party_id 가 여러 사건에 등재될 수 있으므로 first-match 가 아니라 등재된
        사건 집합 전체와 대조한다 (복합 키 도입 후 갱신되지 않았던 부분).
        (case_id, party_id) 자체가 없는 경우는 _check_party_id_references 가 잡는다.
        """
        tx_case = {t["transaction_id"]: t["case_id"] for t in self.transactions}
        assigned = {}                       # party_id -> {등재된 case_id, ...}
        for (case_id, pid) in self.parties:
            assigned.setdefault(pid, set()).add(case_id)

        cross_contam = {}                   # (party_id, foreign_case) -> [tx_id, ...]
        for link in self.links:
            pid, tx_id = link["party_id"], link["transaction_id"]
            tx_case_id = tx_case.get(tx_id)
            if tx_case_id is None or pid not in assigned:
                continue                    # 다른 검사에서 잡음
            if tx_case_id not in assigned[pid]:
                cross_contam.setdefault((pid, tx_case_id), []).append(tx_id)

        issues = []
        for (pid, foreign_case), tx_ids in sorted(cross_contam.items()):
            homes = ", ".join(sorted(assigned[pid]))
            issues.append(IntegrityIssue(
                category=IssueCategory.REFERENTIAL,
                severity=IssueSeverity.CRITICAL,
                message=f"{pid} — {homes} 에 등재됐는데 {foreign_case} 거래에서 참조",
                details=[{"party_id": pid, "등재": homes,
                          "참조한_사건": foreign_case, "tx_ids": tx_ids}],
            ))
        return issues

    def _check_transaction_id_references(self) -> list[IntegrityIssue]:
        """
        2군 ④ : transaction_parties의 transaction_id가 transactions에 존재하는지 확인.
        존재하지 않으면 당사자 연결이 통째로 증발함 (가장 조용한 실패).
        """
        issues = []
        tx_ids = {t["transaction_id"] for t in self.transactions}
        orphan_links = []
        
        for link in self.links:
            if link["transaction_id"] not in tx_ids:
                orphan_links.append({
                    "transaction_id": link["transaction_id"],
                    "party_id": link["party_id"],
                    "role": link["role"]
                })
        
        if orphan_links:
            issues.append(IntegrityIssue(
                category=IssueCategory.REFERENTIAL,
                severity=IssueSeverity.WARNING,
                message="존재하지 않는 거래를 참조하는 당사자 링크들",
                details=orphan_links
            ))
        
        return issues

    def _check_duplicate_keys(self) -> list[IntegrityIssue]:
        """
        2군 ⑤ : 각 테이블의 키 중복 여부.

        cases(case_id), parties((case_id, party_id))는 dict 로 접히면서 뒤 행이
        앞 행을 조용히 덮어쓴다 — relation_type 이 다른 중복 parties 행이 있으면
        판정이 소리 없이 바뀐다. 그래서 dict 가 아니라 원본 행 리스트
        (_raw_cases / _raw_parties)에서 검사한다. load() 가 넘겨준다.

        transaction_parties 는 list 라 접히지 않지만 같은 링크가 중복될 수 있다
        (중복 자체는 판정을 바꾸지 않아 WARNING).

        transactions 도 self.transactions 자체는 list 라 접히지 않지만, 조회부(
        tx_by_id 딕셔너리 컴프리헨션, _trace_inbound_chains 의 next())가 서로 다른
        방식으로 접근한다 — 딕셔너리는 마지막 행을, next()는 첫 행을 고른다.
        transaction_id 가 중복되면 어느 코드 경로를 타느냐에 따라 다른 행이
        조용히 선택되어 판정이 흔들린다. cases/parties 와 같은 이유로 CRITICAL.
        """
        issues = []

        def _dups(rows, keyfn):
            seen, dup = set(), []
            for r in rows:
                k = keyfn(r)
                if k in seen:
                    dup.append(k)
                seen.add(k)
            return dup

        case_dups = _dups(self._raw_cases, lambda r: r["case_id"])
        if case_dups:
            issues.append(IntegrityIssue(
                category=IssueCategory.REFERENTIAL,
                severity=IssueSeverity.CRITICAL,
                message="cases.csv 에 중복된 case_id — 뒤 행이 사건 정의를 조용히 덮어씀",
                details=[{"case_id": c} for c in case_dups],
            ))

        tx_dups = _dups(self.transactions, lambda r: r["transaction_id"])
        if tx_dups:
            issues.append(IntegrityIssue(
                category=IssueCategory.REFERENTIAL,
                severity=IssueSeverity.CRITICAL,
                message="transactions.csv 에 중복된 transaction_id — 조회 방식에 따라 다른 행이 선택되어 판정이 흔들림",
                details=[{"transaction_id": t} for t in tx_dups],
            ))

        party_dups = _dups(self._raw_parties, lambda r: (r["case_id"], r["party_id"]))
        if party_dups:
            issues.append(IntegrityIssue(
                category=IssueCategory.REFERENTIAL,
                severity=IssueSeverity.CRITICAL,
                message="parties.csv 에 중복된 (case_id, party_id) — 관계 사실이 조용히 덮어씌워짐",
                details=[{"case_id": c, "party_id": p} for c, p in party_dups],
            ))

        link_dups = _dups(self.links,
                          lambda r: (r["transaction_id"], r["party_id"], r["role"]))
        if link_dups:
            issues.append(IntegrityIssue(
                category=IssueCategory.REFERENTIAL,
                severity=IssueSeverity.WARNING,
                message="transaction_parties.csv 에 중복된 링크",
                details=[{"transaction_id": t, "party_id": p, "role": r}
                         for t, p, r in link_dups],
            ))

        tx_link_id_dups = _dups(self.tx_links, lambda r: r["link_id"])
        if tx_link_id_dups:
            issues.append(IntegrityIssue(
                category=IssueCategory.REFERENTIAL,
                severity=IssueSeverity.CRITICAL,
                message="transaction_links.csv 에 중복된 link_id",
                details=[{"link_id": link_id} for link_id in tx_link_id_dups],
            ))

        tx_link_edge_dups = _dups(
            self.tx_links,
            lambda r: (r["case_id"], r["from_transaction_id"],
                       r["to_transaction_id"], r["link_type"]),
        )
        if tx_link_edge_dups:
            issues.append(IntegrityIssue(
                category=IssueCategory.REFERENTIAL,
                severity=IssueSeverity.WARNING,
                message="transaction_links.csv 에 중복된 거래 간 링크",
                details=[{
                    "case_id": case_id,
                    "from_transaction_id": from_id,
                    "to_transaction_id": to_id,
                    "link_type": link_type,
                } for case_id, from_id, to_id, link_type in tx_link_edge_dups],
            ))

        return issues

    def _check_case_id_coverage(self) -> list[IntegrityIssue]:
        """
        3군 ⑥ : transactions의 case_id가 cases에 모두 존재하는지 확인.
        이미 run()에서 KeyError로 터지지만, 로드 시점에 잡으면 진단이 낫다.
        """
        issues = []
        case_ids = set(self.cases.keys())
        missing_cases = []
        
        for tx in self.transactions:
            if tx["case_id"] not in case_ids:
                missing_cases.append({
                    "transaction_id": tx["transaction_id"],
                    "case_id": tx["case_id"]
                })
        
        if missing_cases:
            issues.append(IntegrityIssue(
                category=IssueCategory.REFERENTIAL,
                severity=IssueSeverity.CRITICAL,
                message="존재하지 않는 사건을 참조하는 거래들",
                details=missing_cases
            ))
        
        return issues

    def _check_unreferenced_parties(self) -> list[IntegrityIssue]:
        """
        3군 : parties.csv 에 있으나 어떤 거래에서도(어떤 역할로도) 참조되지 않고,
        다른 party 의 officer_of 도 가리키지 않는 당사자.

        판정을 깨뜨리지는 않지만, 검증되지 않는 픽스처가 데이터에 쌓이는 것을
        드러낸다 (officer 픽스처 OFF-* 가 이 방식으로 방치됐던 이력이 있다).
        relation_type == self 는 규약상 상시 등재이므로 제외한다.
        """
        tx_case = {t["transaction_id"]: t["case_id"] for t in self.transactions}
        referenced = {(tx_case.get(l["transaction_id"]), l["party_id"])
                      for l in self.links}
        referenced |= {(cid, (r.get("officer_of") or "").strip())
                       for (cid, _pid), r in self.parties.items()
                       if (r.get("officer_of") or "").strip()}

        orphans = []
        for (case_id, party_id), row in self.parties.items():
            if (case_id, party_id) in referenced:
                continue
            if (row.get("relation_type") or "").strip() == "self":
                continue

            orphans.append({"case_id": case_id, "party_id": party_id,
                            "relation_type": row.get("relation_type")})

        if orphans:
            return [IntegrityIssue(
                category=IssueCategory.REFERENTIAL,
                severity=IssueSeverity.INFO,
                message="어떤 거래에서도 참조되지 않는 당사자 (self 제외)",
                details=orphans,
            )]
        return []

    def _check_share_ratio_sum(self) -> list[IntegrityIssue]:
        """
        값 제약 ⑦ : 같은 (거래, 역할)의 share_ratio 합이 1.0이 아닌 경우.

        transaction_parties.csv 에 share_ratio 컬럼이 있고, 상대방이 복수인 거래
        (T012)는 지분 비율로 배분한다(related_party.yaml multiple_counterparties).
        합이 1.0에서 벗어나면 출연 가치 배분이 조용히 틀어진다.
        부동소수점 합이므로 허용오차 1e-6.

        일부 행만 share_ratio 를 기재한 경우(부분 배분)는 검증할 수 없어 별도
        표시한다 — 전부 기재하거나 전부 비우거나 해야 한다.
        """
        issues = []
        TOL = 1e-6

        by_key = {}   # (tx_id, role) -> [share_ratio 문자열, ...]
        for l in self.links:
            by_key.setdefault((l["transaction_id"], l["role"]), []).append(l.get("share_ratio"))

        mismatch, partial = [], []
        for (tx_id, role), raw in by_key.items():
            vals = [_num(x) for x in raw]
            present = [v for v in vals if v is not None]
            if not present:
                continue                       # 단독 상대방 등 — 배분 안 함
            if len(present) != len(vals):
                partial.append({"transaction_id": tx_id, "role": role,
                                "기재": len(present), "전체": len(vals)})
                continue
            total = sum(present)
            if abs(total - 1.0) > TOL:
                mismatch.append({"transaction_id": tx_id, "role": role,
                                 "share_ratio_합": round(total, 6),
                                 "상대방_수": len(present)})

        if mismatch:
            issues.append(IntegrityIssue(
                category=IssueCategory.VALUE_CONSTRAINT,
                severity=IssueSeverity.WARNING,
                message="share_ratio 합이 1.0이 아님 (share_sum_mismatch) — 출연 가치 배분이 틀어짐",
                details=mismatch,
            ))
        if partial:
            issues.append(IntegrityIssue(
                category=IssueCategory.VALUE_CONSTRAINT,
                severity=IssueSeverity.WARNING,
                message="같은 (거래, 역할)에서 일부만 share_ratio 기재 — 배분 검증 불가",
                details=partial,
            ))
        return issues

    def _check_tx_link_references(self) -> list[IntegrityIssue]:
        """
        transaction_links 참조 무결성:
        - from/to transaction_id 가 transactions 에 존재하는가
        - from/to 거래가 모두 link.case_id 와 일치하는가 (교차 사건 오염 방지)
        parties_of() 와 같은 이유로 case_id 를 명시적으로 요구한다.
        """
        issues = []
        tx_by_id = {t["transaction_id"]: t for t in self.transactions}
        orphan, cross_contam = [], []

        for l in self.tx_links:
            from_tx = tx_by_id.get(l["from_transaction_id"])
            to_tx = tx_by_id.get(l["to_transaction_id"])

            if from_tx is None or to_tx is None:
                orphan.append({
                    "link_id": l["link_id"],
                    "from_transaction_id": l["from_transaction_id"],
                    "to_transaction_id": l["to_transaction_id"],
                    "missing": ("from" if from_tx is None else "") +
                               ("," if from_tx is None and to_tx is None else "") +
                               ("to" if to_tx is None else "")
                })
                continue

            if from_tx["case_id"] != l["case_id"] or to_tx["case_id"] != l["case_id"]:
                cross_contam.append({
                    "link_id": l["link_id"],
                    "link_case_id": l["case_id"],
                    "from_case_id": from_tx["case_id"],
                    "to_case_id": to_tx["case_id"],
                })

        if orphan:
            issues.append(IntegrityIssue(
                category=IssueCategory.REFERENTIAL,
                severity=IssueSeverity.WARNING,
                message="존재하지 않는 거래를 참조하는 transaction_links",
                details=orphan
            ))
        if cross_contam:
            issues.append(IntegrityIssue(
                category=IssueCategory.REFERENTIAL,
                severity=IssueSeverity.CRITICAL,
                message="link.case_id 와 참조 거래의 case_id 가 불일치 (교차 사건 오염)",
                details=cross_contam
            ))
        return issues

    def _check_tx_link_temporal_order(self) -> list[IntegrityIssue]:
        """
        자금은 미래에서 과거로 흐를 수 없다.
        from 거래일 > to 거래일이면 데이터 입력 오류가 확실하다.
        """
        issues = []
        tx_by_id = {t["transaction_id"]: t for t in self.transactions}
        reversed_links = []

        for l in self.tx_links:
            from_tx = tx_by_id.get(l["from_transaction_id"])
            to_tx = tx_by_id.get(l["to_transaction_id"])
            if from_tx is None or to_tx is None:
                continue  # _check_tx_link_references 에서 이미 잡음

            fd, td = _date(from_tx["transaction_date"]), _date(to_tx["transaction_date"])
            if fd and td and fd > td:
                reversed_links.append({
                    "link_id": l["link_id"],
                    "from_transaction_id": l["from_transaction_id"],
                    "from_date": str(fd),
                    "to_transaction_id": l["to_transaction_id"],
                    "to_date": str(td),
                })

        if reversed_links:
            issues.append(IntegrityIssue(
                category=IssueCategory.REFERENTIAL,
                severity=IssueSeverity.CRITICAL,
                message="시간 역행 링크 — from 거래일이 to 거래일보다 늦음",
                details=reversed_links
            ))
        return issues

    def _check_tx_link_self_reference(self) -> list[IntegrityIssue]:
        """자기 자신을 참조하는 링크."""
        issues = []
        self_refs = [
            {"link_id": l["link_id"], "transaction_id": l["from_transaction_id"]}
            for l in self.tx_links
            if l["from_transaction_id"] == l["to_transaction_id"]
        ]
        if self_refs:
            issues.append(IntegrityIssue(
                category=IssueCategory.REFERENTIAL,
                severity=IssueSeverity.CRITICAL,
                message="자기 자신을 참조하는 transaction_links",
                details=self_refs
            ))
        return issues

    def _check_tx_link_cycles(self) -> list[IntegrityIssue]:
        """같은 날짜에는 시간 역행 검사로 잡히지 않는 모든 순환 구성요소를 검출한다."""
        tx_ids = {t["transaction_id"] for t in self.transactions}
        by_case = {}
        for link in self.tx_links:
            from_id, to_id = link["from_transaction_id"], link["to_transaction_id"]
            if from_id not in tx_ids or to_id not in tx_ids or from_id == to_id:
                continue
            by_case.setdefault(link["case_id"], {}).setdefault(from_id, set()).add(to_id)

        cycles = []
        for case_id, graph in sorted(by_case.items()):
            # Tarjan SCC: 순환에 속하는 노드 집합을 빠짐없이 한 번씩 보고한다.
            # 한 SCC 안에 단순 순환이 여러 개 있어도 하나의 수정 단위로 제시한다.
            index = 0
            indices, lowlinks = {}, {}
            stack, on_stack = [], set()

            def strongconnect(node):
                nonlocal index
                indices[node] = lowlinks[node] = index
                index += 1
                stack.append(node)
                on_stack.add(node)

                for nxt in sorted(graph.get(node, ())):
                    if nxt not in indices:
                        strongconnect(nxt)
                        lowlinks[node] = min(lowlinks[node], lowlinks[nxt])
                    elif nxt in on_stack:
                        lowlinks[node] = min(lowlinks[node], indices[nxt])

                if lowlinks[node] != indices[node]:
                    return
                component = []
                while True:
                    member = stack.pop()
                    on_stack.remove(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1:
                    cycles.append({"case_id": case_id,
                                   "transactions": sorted(component)})

            nodes = set(graph) | {n for tos in graph.values() for n in tos}
            for node in sorted(nodes):
                if node not in indices:
                    strongconnect(node)

        if not cycles:
            return []
        return [IntegrityIssue(
            category=IssueCategory.REFERENTIAL,
            severity=IssueSeverity.CRITICAL,
            message="transaction_links 에 순환 구성요소 존재 — 자금 흐름 DAG 위반",
            details=sorted(cycles, key=lambda item: (item["case_id"], item["transactions"])),
        )]

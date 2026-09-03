"""
trustee-fds 판정 실행기

설계 원칙
---------
1. 조문에서 나오는 값(소급기간, 기준 사건, 행위 유형, 임계값, 정렬 구획)은
   전부 YAML에서 읽는다. 코드에 숫자를 박지 않는다.

2. 술어(predicate)는 id 로 등록한다. YAML 의 `when` 문자열은 사람이 읽는
   명세이고, 실행은 같은 id 로 등록된 파이썬 함수가 한다.
   YAML 에 있는 id 가 코드에 없으면 즉시 실패한다 (UnimplementedPredicate).
   → 완전한 표현식 언어를 만들지 않은 대신, 룰과 코드의 어긋남이
     조용히 넘어가지 않도록 강제한다.

3. 각 거래가 '몇 단계까지 갔는지'를 기록한다. 테스트 케이스가 자기가
   검증하려는 단계에 도달했는지 확인하기 위한 것이다.

4. 무결성 검사는 Dataset 로드 시점에 수행한다. 데이터의 일관성은 데이터가
   만들어지는 순간 보장되어야 한다.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path

import yaml
from dateutil.relativedelta import relativedelta

# 단계 순서. target_stage 검사에 쓴다.
STAGE_ORDER = {"temporal": 1, "party": 1, "action": 2, "economic": 3,
               "ranking": 4, "signal": 4}


class UnimplementedPredicate(Exception):
    """YAML 에 선언된 술어가 코드에 없을 때."""


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
        return float(s) if s else None
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
        return issues

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
                severity=IssueSeverity.INFO,
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
        referenced = {l["party_id"] for l in self.links}
        referenced |= {(r.get("officer_of") or "").strip()
                       for r in self.parties.values()} - {""}

        orphans = []
        for (case_id, party_id), row in self.parties.items():
            if party_id in referenced:
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


def load_rules(root: Path):
    rules = {}
    for p in (root / "rules").glob("*.yaml"):
        r = yaml.safe_load(p.read_text(encoding="utf-8"))
        rules[r["rule_id"]] = r
    main = rules["art391-4-gratuitous"]
    for dep in main.get("depends_on", []):
        if dep["rule_id"] not in rules:
            raise KeyError(f"의존 룰 누락: {dep['rule_id']}")
    return rules


# ------------------------------------------------------- 1단계: 관계·시간

def _boundary_window_days(rp_rules):
    """관계 종료일 전후 relation_boundary 플래그를 붙일 폭(일). related_party.yaml."""
    return rp_rules.get("temporal_scope", {}).get("boundary_window_days", 30)


def _apply_temporal_window(verdict, flags, party, tx_date, rp_rules):
    """
    관계 유효기간(relation_valid_from/to) 밖의 거래는 not_related 로 되돌린다.
    former_spouse 를 제외한 모든 관계 유형이 공유하는 로직 — former_spouse 는
    혼인기간을 related 로 취급해야 해서 자체 함수에서 이 로직을 직접 수행한다.
    """
    frm, to = _date(party.get("relation_valid_from")), _date(party.get("relation_valid_to"))
    if verdict != "not_related":
        if frm and tx_date < frm:
            verdict, flags = "not_related", flags + ["relation_not_yet"]
        elif to and tx_date > to:
            verdict, flags = "not_related", flags + ["relation_ended"]
    if to and abs((tx_date - to).days) <= _boundary_window_days(rp_rules):
        flags = flags + ["relation_boundary"]
    if not frm and not to and verdict != "not_related":
        flags = flags + ["relation_period_unknown"]
    if verdict == "verify":
        flags = flags + [rp_rules["verify_behavior"]["flag"]]
    return verdict, flags


def _classify_affiliate(party, rp_rules):
    """
    지분율 기준 동적 판정. ownership_test.threshold_percent 이상이면 related.
    미상이면 verify(사실 데이터 부재), 미달이면 verify(사실상 영향력 배제 못 함) —
    임계값 미달을 not_related 로 자동 확정하지 않는다.
    """
    ot = rp_rules.get("ownership_test", {})
    threshold = ot.get("threshold_percent", 30)
    pct = _num(party.get("ownership_percentage"))
    if pct is None:
        return "verify", ["ownership_percentage_unknown"]
    if pct >= threshold:
        return "related", []
    return "verify", ["ownership_below_threshold_de_facto_control_unassessed"]


def _classify_officer(party, rp_rules, case_id, all_parties, debtor_type):
    """
    officer_of(party_id 참조)로 동적 판정.
    - 채무자 법인 자신(relation_type=self)을 가리키면: 법인 채무자에 한해 related.
    - affiliate 를 가리키면: 그 법인의 ownership_test 판정을 물려받는다.
    - 'none'(무관한 법인)을 가리키면: not_related.
    - 대상을 특정할 수 없으면: verify.
    """
    target_id = (party.get("officer_of") or "").strip()
    if not target_id or all_parties is None or case_id is None:
        return "verify", ["officer_of_unknown"]

    target = all_parties.get((case_id, target_id))
    if target is None:
        return "verify", ["officer_of_unresolved"]

    target_rt = (target.get("relation_type") or "").strip()

    if target_rt == "self":
        if debtor_type == "corporation":
            return "related", ["officer_of_debtor_itself"]
        # 개인 채무자는 '임원' 개념 자체가 성립하지 않는다 — 데이터 불일치 신호.
        return "verify", ["officer_of_individual_debtor_data_inconsistency"]

    if target_rt == "affiliate":
        verdict, _ = _classify_affiliate(target, rp_rules)
        return verdict, [f"officer_of_affiliate_{verdict}"]

    if target_rt == "none":
        return "not_related", []

    return "verify", ["officer_of_relation_unclassified"]


def _classify_former_spouse(party, tx_date, rp_rules):
    """
    혼인 기간(relation_valid_from~relation_valid_to) 중 거래는 배우자와 동일하게
    related. 이혼 후 거래는 원칙 not_related 이나, post_divorce_dependency = Y
    (다목 — 본인의 금전·재산으로 생계 유지)이면 related. 그 필드가 비어 있으면 verify.

    동일인 여부(예: 샘플 P-007이 P-001과 동일인인지)는 사실관계 조사 문제이므로
    이 함수는 판정하지 않는다 — parties.csv 에 별도 party 로 등재된 그대로 다룬다.
    """
    frm = _date(party.get("relation_valid_from"))
    to = _date(party.get("relation_valid_to"))
    dep = (party.get("post_divorce_dependency") or "").strip().upper()
    flags = []

    if frm and tx_date < frm:
        return "not_related", ["relation_not_yet"]

    if to is None or tx_date <= to:
        # 혼인 기간 중(또는 종료일 미상) — 배우자와 동일하게 취급
        return "related", flags

    if abs((tx_date - to).days) <= _boundary_window_days(rp_rules):
        flags.append("relation_boundary")

    if dep == "Y":
        return "related", flags + ["post_divorce_dependency_confirmed"]
    if dep == "N":
        return "not_related", flags + ["relation_ended"]
    return "verify", flags + [rp_rules["verify_behavior"]["flag"], "post_divorce_dependency_unknown"]


def classify_relation(party, tx_date, rp_rules, case_id=None, all_parties=None, debtor_type=None):
    """
    관계라는 '사실'을 특수관계인 해당 여부라는 '판단'으로 옮긴다.

    former_spouse / affiliate / officer 는 parties.csv 의 구조화 필드로 동적
    판정한다(§related_party.yaml ownership_test). 그 외 유형은 classification
    딕셔너리의 정적 verdict 를 시간 창(_apply_temporal_window)에 통과시킨다.
    """
    rt = (party.get("relation_type") or "").strip()

    if rt == "former_spouse":
        return _classify_former_spouse(party, tx_date, rp_rules)
    if rt == "affiliate":
        verdict, flags = _classify_affiliate(party, rp_rules)
        return _apply_temporal_window(verdict, flags, party, tx_date, rp_rules)
    if rt == "officer":
        verdict, flags = _classify_officer(party, rp_rules, case_id, all_parties, debtor_type)
        return _apply_temporal_window(verdict, flags, party, tx_date, rp_rules)

    entry = rp_rules["classification"].get(rt)
    verdict = entry["verdict"] if entry else rp_rules["unknown_relation_type"]
    return _apply_temporal_window(verdict, [], party, tx_date, rp_rules)


def resolve_lookback(verdicts, rule):
    """소급기간. legal_counterparty 의 판정만 본다. (2008다48117)"""
    lb = rule["temporal_filter"]["lookback"]
    key = "related" if any(v == "related" for v in verdicts) else (
        "verify" if any(v == "verify" for v in verdicts) else "not_related")
    case = lb["cases"][key]
    return case["months"], case["basis"], key


def temporal_window(case_row, months, rule):
    anchors = []
    for a in rule["temporal_filter"]["anchor_events"]:
        fld = a["field"].split(".", 1)[1]
        dt = _date(case_row.get(fld))
        if dt:
            anchors.append((a["label"], dt))
    if not anchors:
        raise ValueError("기준 사건이 하나도 없다")
    start = min(dt - relativedelta(months=months) for _, dt in anchors)
    upper = _date(case_row.get(rule["temporal_filter"]["upper_bound"]["field"].split(".", 1)[1]))
    return start, anchors, upper


def _debt_repayment_within_tolerance(tx, route_rule):
    """
    자기 채무 변제가 허용오차 이내인지 확인한다. '실제로 지급한 금액'
    (consideration_paid)과 '실제로 채무가 줄어든 금액'(liability_reduction)을
    비교한다. 허용오차를 벗어나면(과다변제) route_out 하지 않는다 —
    route_out 은 '확실히 무상이 아닌' 거래만 배제해야 하기 때문이다.

    비교할 데이터가 없으면 정상 변제로 단정하지 않는다(False 반환 — 3단계로
    보내 결측을 드러낸다. route_out 은 조용히 배제하는 통로이므로, 근거 없이
    통과시키지 않는다).
    """
    paid = _num(tx.get("consideration_paid"))
    reduced = _num(tx.get("liability_reduction"))
    if paid is None or reduced is None or paid == 0:
        return False
    tolerance = route_rule.get("tolerance", 0.02)
    return abs(paid - reduced) / paid <= tolerance


# ------------------------------------------------------- 3단계: 경제 실질

def economics(tx, rule):
    """Value Out / Value In. 채무자 기준으로만 계산한다."""
    outs = [_num(tx.get("asset_fair_value")),
            _num(tx.get("liability_increase_value")),
            _num(tx.get("waived_right_value"))]
    have = [v for v in outs if v is not None]
    value_out = sum(have) if have else None

    cash = _num(tx.get("consideration_paid")) or 0.0
    if tx.get("payment_verified") != "Y":
        cash = 0.0                                   # 검증되지 않은 대가는 산입하지 않는다
    benefit = _num(tx.get("debtor_direct_benefit_value")) or 0.0
    if tx.get("benefit_realizability") != "realized":
        benefit = 0.0                                # 추상적 기대는 Value In 이 아니다

    notes = []
    if benefit and cash and abs(benefit - cash) < 1e-6:
        benefit = 0.0                                # 동일 항목의 이중계상 방지
        notes.append("direct_benefit == consideration_paid — 이중계상 회피")
    value_in = cash + benefit

    ratio = (value_in / value_out) if value_out else None
    return value_out, value_in, ratio, notes


def triage(ratio, rule):
    bands = rule["economic_substance"]["ratio"]["triage_bands"]
    if ratio is None:
        for b in bands:
            if "when" in b:
                return b["priority"]
        return "unresolved"
    # ratio 가 있으면 'when' 밴드(undefined 전용)는 건너뛴다.
    # 이 조건을 빠뜨리면 max 키가 없는 when 밴드가 catch-all 로 오인되어
    # 모든 항목이 첫 밴드로 떨어진다.
    for b in (x for x in bands if "when" not in x):
        if b.get("max") is None or ratio <= b["max"]:
            return b["priority"]
    return "low"


def triage_by_signal_count(signal_count, rule):
    """
    ratio 기반 triage_bands 가 척도로서 성립하지 않는 행위 유형(예: 보증)에 쓴다.
    '보증료율이 몇 %면 정상'이라는 근거 없는 숫자 대신, 이미 있는 다른 신호의
    중첩 정도로 우선순위를 매긴다.
    """
    bands = rule["economic_substance"]["ratio"]["signal_count_bands"]
    for b in bands:
        if b.get("max") is None or signal_count <= b["max"]:
            return b["priority"]
    return "low"


def triage_priority(action_type, ratio, signal_count, rule):
    """
    action_type 이 applicable_action_types 안이면 비율 기반(triage), 밖이면
    신호 개수 기반(triage_by_signal_count)으로 우선순위를 매긴다.
    T103(보증료율 0.04)이 매각과 같은 비율 밴드로 잘못 high 처리되던 문제를 고친다.

    ratio 가 None(value_out 산출 불가 — "평가 필요")인 경우는 행위 유형과 무관하게
    항상 unresolved 다. 이건 대가비율이 무의미한 행위인지의 문제가 아니라, 애초에
    비교할 값 자체가 없다는 뜻이기 때문이다.
    """
    if ratio is None:
        return "unresolved"
    scope = rule["economic_substance"]["ratio"].get("applicable_action_types")
    if scope is None or action_type in scope:
        return triage(ratio, rule)
    return triage_by_signal_count(signal_count, rule)


def undervalue_verdict(tx, ratio, rule):
    ua = rule["economic_substance"].get("undervalue_assessment")
    if not ua or tx["action_type"] not in ua["applies_to"]:
        return "candidate"
    if ratio is None:
        return "unresolved"
    threshold = ua["threshold"]     # YAML 이 실행값을 명시한다. when 문자열은 명세.
    return "candidate" if ratio <= threshold else "not_candidate"


def _trace_inbound_chains(ds, case_id, tx_id, tx_date, ffa, confidence_rank,
                           depth, visited):
    """
    tx_id 로 자금이 흘러들어온 경로를 max_hops 까지 역방향으로 추적한다.

    약한 고리 원칙: 각 홉마다 min_confidence 와 proximity_window_days 를 독립
    적용한다. confidence 미달인 홉에서는 그 경로 추적을 중단하고(신호로 세우지
    않음), 시간창을 넘는 홉이 하나라도 있으면 그 경로 전체를 지연으로 표시한다.

    반환: (in_window_hits: list[int], delayed_hits: list[int])
          각 리스트는 신호가 성립한 홉 수(1=직접 연결, 2 이상=다단계)를 담는다.
    """
    min_rank = confidence_rank[ffa["min_confidence"]]
    window = ffa["proximity_window_days"]
    max_hops = ffa.get("max_hops", 1)
    link_types = ffa["eligible_link_types"]

    in_window, delayed = [], []
    if depth > max_hops:
        return in_window, delayed

    for link in ds.inbound_fund_flows(case_id, tx_id, link_types):
        if confidence_rank.get(link["confidence"], -1) < min_rank:
            continue  # 약한 고리 — 이 경로는 여기서 끊는다

        from_id = link["from_transaction_id"]
        if from_id in visited:
            continue  # cycle_policy: skip_visited

        from_tx = next((t for t in ds.transactions
                         if t["transaction_id"] == from_id), None)
        if from_tx is None:
            continue  # 무결성 검사가 별도로 잡는다

        fd = _date(from_tx["transaction_date"])
        if fd is None or tx_date is None:
            continue

        within = (tx_date - fd).days <= window
        if within:
            in_window.append(depth)
        else:
            delayed.append(depth)

        # 이 홉의 출발 거래에서 다시 거슬러 올라간다
        sub_in, sub_delayed = _trace_inbound_chains(
            ds, case_id, from_id, fd, ffa, confidence_rank,
            depth + 1, visited | {from_id})
        # 상류 홉이 시간창을 넘었으면 경로 전체가 지연이다 (약한 고리)
        if within:
            in_window += sub_in
        else:
            delayed += sub_in
        delayed += sub_delayed

    return in_window, delayed


def evaluate_funds_circled_back(ds, case_id, tx, rule):
    """
    이 거래로 자금이 흘러들어온(inbound funds_flow) 경로가 있는지, 그리고
    그것이 신호로 인정되는지 판정한다. 링크는 사실, 이 함수는 판단.

    max_hops 까지 다단계 연쇄(layering)를 추적한다 — 은닉은 한 번에 끝나지 않는다.

    반환: (circled_back: bool, flags: list[str])
    """
    ffa = rule.get("fund_flow_analysis")
    if not ffa:
        return False, []

    # confidence 서열은 YAML 이 정한다 (README §4 confidence 어휘와 일치해야 함).
    confidence_rank = {c: i for i, c in enumerate(ffa["confidence_order"])}
    tx_id = tx["transaction_id"]
    td = _date(tx["transaction_date"])

    in_window, delayed = _trace_inbound_chains(
        ds, case_id, tx_id, td, ffa, confidence_rank, depth=1, visited={tx_id})

    flags = []
    if delayed:
        flags.append(ffa["outside_window_flag"])  # 배제하지 않고 표시만 (soft)
    # 2홉 이상으로 잡힌 경우 직접 연결과 증거 강도가 다르므로 구별해 표시한다
    if any(hops >= 2 for hops in in_window) and "multi_hop_flag" in ffa:
        flags.append(ffa["multi_hop_flag"])

    return bool(in_window), flags


# ------------------------------------------------------------ 신호 술어

PREDICATES = {}


def predicate(pid):
    def deco(fn):
        PREDICATES[pid] = fn
        return fn
    return deco


@predicate("no_consideration")
def _p1(c): return c["value_in"] == 0


@predicate("payment_unverified")
def _p2(c):
    return (_num(c["tx"].get("consideration_contractual")) or 0) > 0 \
        and c["tx"].get("payment_verified") != "Y"


@predicate("related_counterparty")
def _p3(c): return "related" in c["cp_verdicts"]


@predicate("role_split_guarantee")
def _p4(c):
    pd = {p["party_id"] for p in c["principal_debtors"]}
    cp = {p["party_id"] for p in c["counterparties"]}
    return bool(pd) and not pd <= cp


@predicate("related_principal_debtor")
def _p5(c): return "related" in c["pd_verdicts"]


@predicate("funds_circled_back")
def _p6(c):
    return c["funds_circled_back"]


@predicate("non_cash_waiver")
def _p7(c): return _num(c["tx"].get("waived_right_value")) is not None


@predicate("proximity_before")
def _p8(c): return c["days_to_anchor"] is not None and 0 < c["days_to_anchor"] <= 30


@predicate("post_anchor")
def _p9(c): return c["days_to_anchor"] is not None and c["days_to_anchor"] <= 0


@predicate("no_valuation")
def _p10(c): return c["value_out"] is None


@predicate("no_necessity_evidence")
def _p11(c): return not (c["tx"].get("necessity_evidence") or "").strip()


def check_predicate_coverage(rule):
    missing = [s["id"] for s in rule["priority_signals"] if s["id"] not in PREDICATES]
    if missing:
        raise UnimplementedPredicate(
            "YAML 에 선언됐으나 구현이 없는 술어: " + ", ".join(missing))


# ---------------------------------------------------------------- 파이프라인

@dataclass
class Result:
    tx_id: str
    reached: str = "temporal"      # 도달한 최종 단계
    candidate: bool = False
    lookback_months: int | None = None
    lookback_key: str | None = None
    priority: str | None = None
    value_out: float | None = None
    value_in: float | None = None
    ratio: float | None = None
    signals: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    drop_reason: str | None = None


def run(ds: Dataset, rules: dict) -> list[Result]:
    rule = rules["art391-4-gratuitous"]
    rp = rules["related-party-classification"]
    check_predicate_coverage(rule)

    af = rule["action_filter"]
    boundary_days = rule["temporal_filter"]["boundary_days"]
    results = []

    for tx in ds.transactions:
        res = Result(tx_id=tx["transaction_id"])
        case = ds.cases[tx["case_id"]]
        case_id = tx["case_id"]
        td = _date(tx["transaction_date"])

        # --- 1단계: 관계 → 소급기간 → 시간 창
        cps = ds.parties_of(case_id, tx["transaction_id"], "legal_counterparty")
        pds = ds.parties_of(case_id, tx["transaction_id"], "principal_debtor")
        debtor_type = case.get("debtor_type")
        cp_v, pd_v = [], []
        for p in cps:
            v, fl = classify_relation(p, td, rp, case_id=case_id, all_parties=ds.parties,
                                       debtor_type=debtor_type)
            cp_v.append(v)
            res.flags += fl
        for p in pds:
            v, _ = classify_relation(p, td, rp, case_id=case_id, all_parties=ds.parties,
                                      debtor_type=debtor_type)
            pd_v.append(v)

        months, basis, key = resolve_lookback(cp_v, rule)
        res.lookback_months, res.lookback_key = months, key
        start, anchors, upper = temporal_window(case, months, rule)

        if td < start:
            res.drop_reason = f"시간 창 밖 (창 시작 {start})"
            results.append(res)
            continue
        nearest = min(anchors, key=lambda a: abs((a[1] - td).days))
        days_to_anchor = (nearest[1] - td).days
        if abs(days_to_anchor) <= boundary_days or abs((td - start).days) <= boundary_days:
            res.flags.append("boundary_case")
        if upper and td > upper:
            res.flags.append("post_adjudication_soft")   # 배제하지 않고 표시만

        # --- 2단계: 행위 유형
        res.reached = "action"
        at = tx["action_type"]
        # route_out 은 문자열 매칭이 아니라 조건을 실제로 평가한다.
        # debt_repayment 는 숫자 조건(허용오차)이 있어 별도 함수로 판정하고,
        # 그 외(action_type 만으로 결정되는 규칙)는 문자열 매칭으로 충분하다.
        routed = None
        for r in af["route_out"]:
            if f'"{at}"' not in str(r["when"]):
                continue
            if at == "debt_repayment":
                if _debt_repayment_within_tolerance(tx, r):
                    routed = r
                # 허용오차를 벗어나면(과다변제) routed 를 세우지 않는다 —
                # route_out 하지 않고 아래로 흘려보내 후보 행위 유형 검사를 받는다.
            else:
                routed = r
            break
        if routed:
            res.drop_reason = f"{routed['to']} 로 라우팅"
            results.append(res)
            continue
        if at not in af["candidate_types"]:
            res.drop_reason = f"후보 행위 유형 아님 ({at})"
            results.append(res)
            continue

        # --- 3단계: 경제적 실질
        res.reached = "economic"
        vo, vi, ratio, notes = economics(tx, rule)
        res.value_out, res.value_in, res.ratio = vo, vi, ratio
        res.flags += notes
        verdict = undervalue_verdict(tx, ratio, rule)
        if verdict == "not_candidate":
            res.drop_reason = f"대가비율 {ratio:.4f} — 정상 대가 범위"
            results.append(res)
            continue

        # --- 4단계: 정렬 구획 + 신호
        res.reached = "ranking"
        res.candidate = True
        circled_back, ff_flags = evaluate_funds_circled_back(ds, case_id, tx, rule)
        res.flags += ff_flags
        ctx = dict(tx=tx, value_in=vi, value_out=vo, cp_verdicts=cp_v, pd_verdicts=pd_v,
                   counterparties=cps, principal_debtors=pds, days_to_anchor=days_to_anchor,
                   funds_circled_back=circled_back)
        res.signals = [s["id"] for s in rule["priority_signals"] if PREDICATES[s["id"]](ctx)]
        # 신호를 먼저 계산해야 triage_priority 가 signal_count_bands 를 쓸 수 있다
        # (applicable_action_types 밖의 행위, 예: 보증).
        res.priority = ("unresolved" if verdict == "unresolved"
                         else triage_priority(at, ratio, len(res.signals), rule))
        results.append(res)

    return results


def partition(results, rule):
    """value_out 결측 항목을 목록 아래로 밀지 않는다."""
    cands = [r for r in results if r.candidate]
    unvalued = [r for r in cands if r.value_out is None]
    valued = [r for r in cands if r.value_out is not None]
    unvalued.sort(key=lambda r: (-len(r.signals), r.tx_id))
    valued.sort(key=lambda r: -(r.value_out - r.value_in))
    return unvalued, valued

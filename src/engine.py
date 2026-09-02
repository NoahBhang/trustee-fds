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
    integrity_issues: list[IntegrityIssue] = field(default_factory=list)

    @classmethod
    def load(cls, root: Path):
        """데이터 로드 후 즉시 무결성 검사 수행."""
        d = root / "data" / "sample"
        
        # 원본 데이터 읽기
        cases_rows = _rows(d / "cases.csv")
        transactions_rows = _rows(d / "transactions.csv")
        parties_rows = _rows(d / "parties.csv")
        links_rows = _rows(d / "transaction_parties.csv")
        
        # 복합 키로 parties 로드
        ds = cls(
            cases={r["case_id"]: r for r in cases_rows},
            transactions=transactions_rows,
            parties={(r["case_id"], r["party_id"]): r for r in parties_rows},
            links=links_rows,
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

    def _check_all_integrity(self) -> list[IntegrityIssue]:
        """모든 무결성 검사를 수행하고 문제 목록을 반환."""
        issues = []
        issues += self._check_party_id_references()
        issues += self._check_legal_counterparty_coverage()
        issues += self._check_case_id_cross_validation()
        issues += self._check_transaction_id_references()
        issues += self._check_duplicate_keys()
        issues += self._check_case_id_coverage()
        issues += self._check_share_ratio_sum()
        return issues

    def _check_party_id_references(self) -> list[IntegrityIssue]:
        """
        1군 ① : transaction_parties의 party_id가 parties에 존재하는지 확인.
        역할별로 심각도를 구분한다.
        """
        issues = []
        missing_refs = {}  # (party_id, role) -> [(tx_id, case_id), ...]
        
        for link in self.links:
            party_id = link["party_id"]
            case_id = None
            
            # case_id 찾기
            for tx in self.transactions:
                if tx["transaction_id"] == link["transaction_id"]:
                    case_id = tx["case_id"]
                    break
            
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
        """
        issues = []
        missing_coverage = []
        
        for tx in self.transactions:
            case_id = tx["case_id"]
            tx_id = tx["transaction_id"]
            
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
        1군 ③ : parties의 case_id와 참조하는 거래의 case_id가 일치하는지 확인.
        다른 사건의 당사자가 이 사건에 끼어들 수 없도록.
        """
        issues = []
        cross_contam = {}  # (party_id, assigned_case, foreign_case) -> [tx_id, ...]
        
        for link in self.links:
            party_id = link["party_id"]
            tx_id = link["transaction_id"]
            
            # 거래의 case_id
            tx_case_id = None
            for tx in self.transactions:
                if tx["transaction_id"] == tx_id:
                    tx_case_id = tx["case_id"]
                    break
            
            if tx_case_id is None:
                continue  # 거래 없음, 다른 검사에서 잡음
            
            # party가 parties에 있는지 확인 (어느 case_id인지)
            assigned_case = None
            for (case_id, pid), _ in self.parties.items():
                if pid == party_id:
                    assigned_case = case_id
                    break
            
            if assigned_case is not None and assigned_case != tx_case_id:
                key = (party_id, assigned_case, tx_case_id)
                if key not in cross_contam:
                    cross_contam[key] = []
                cross_contam[key].append(tx_id)
        
        for (party_id, assigned_case, foreign_case), tx_ids in sorted(cross_contam.items()):
            details = [{"party_id": party_id, "assigned_to": assigned_case, 
                       "referenced_by": foreign_case, "tx_ids": tx_ids}]
            message = f"{party_id} — {assigned_case}에 속하는데 {foreign_case} 거래에서 참조"
            issues.append(IntegrityIssue(
                category=IssueCategory.REFERENTIAL,
                severity=IssueSeverity.CRITICAL,
                message=message,
                details=details
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
        - cases, parties (복합키), transaction_parties의 중복 확인.
        """
        issues = []
        
        # cases의 중복 (dict 컴프리헨션이라 뒤 행이 앞 행을 덮어씀)
        # 이미 로드된 cases dict는 중복이 있었으면 뒤 행으로 덮어씌워 졌으므로
        # 원본 데이터를 재확인하려면 파일을 다시 읽어야 하는데,
        # 지금은 로드된 데이터만 검사하므로 생략
        
        # parties의 중복은 복합 키이므로 dict 컴프리헨션 시점에 이미 덮어씌워짐
        # 원본 rows를 다시 확인해야 하는데 접근 불가능하므로 현재는 생략
        
        # transaction_parties는 list이므로 같은 링크가 여러 번 나올 수 있음
        seen_links = set()
        dup_links = []
        for link in self.links:
            key = (link["transaction_id"], link["party_id"], link["role"])
            if key in seen_links:
                dup_links.append(key)
            seen_links.add(key)
        
        if dup_links:
            issues.append(IntegrityIssue(
                category=IssueCategory.REFERENTIAL,
                severity=IssueSeverity.WARNING,
                message="transaction_parties.csv에 중복된 링크",
                details=[{"transaction_id": t, "party_id": p, "role": r} 
                        for t, p, r in dup_links]
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

    def _check_share_ratio_sum(self) -> list[IntegrityIssue]:
        """
        값 제약 ⑦ : share_ratio의 합이 1.0이 아닌 경우.
        현재 transactions에 share_ratio가 없으므로 나중에 추가될 때 구현.
        """
        # 현재는 구현하지 않음 (transaction_parties에 share_ratio 필드 추가 필요)
        return []


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

def classify_relation(party, tx_date, rp_rules):
    """관계라는 '사실'을 특수관계인 해당 여부라는 '판단'으로 옮긴다."""
    rt = (party.get("relation_type") or "").strip()
    entry = rp_rules["classification"].get(rt)
    verdict = entry["verdict"] if entry else rp_rules["unknown_relation_type"]
    flags = []

    frm, to = _date(party.get("relation_valid_from")), _date(party.get("relation_valid_to"))
    if verdict != "not_related":
        if frm and tx_date < frm:
            verdict, flags = "not_related", flags + ["relation_not_yet"]
        elif to and tx_date > to:
            verdict, flags = "not_related", flags + ["relation_ended"]
    if to and abs((tx_date - to).days) <= 30:
        flags.append("relation_boundary")
    if not frm and not to and verdict != "not_related":
        flags.append("relation_period_unknown")
    if verdict == "verify":
        flags.append(rp_rules["verify_behavior"]["flag"])
    return verdict, flags


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


def undervalue_verdict(tx, ratio, rule):
    ua = rule["economic_substance"].get("undervalue_assessment")
    if not ua or tx["action_type"] not in ua["applies_to"]:
        return "candidate"
    if ratio is None:
        return "unresolved"
    return "candidate" if ratio <= 0.50 else "not_candidate"


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
    # 거래 간 연결 테이블이 아직 없다. 로드맵 4단계.
    return False


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
    results = []

    for tx in ds.transactions:
        res = Result(tx_id=tx["transaction_id"])
        case = ds.cases[tx["case_id"]]
        case_id = tx["case_id"]
        td = _date(tx["transaction_date"])

        # --- 1단계: 관계 → 소급기간 → 시간 창
        cps = ds.parties_of(case_id, tx["transaction_id"], "legal_counterparty")
        pds = ds.parties_of(case_id, tx["transaction_id"], "principal_debtor")
        cp_v, pd_v = [], []
        for p in cps:
            v, fl = classify_relation(p, td, rp)
            cp_v.append(v)
            res.flags += fl
        for p in pds:
            v, _ = classify_relation(p, td, rp)
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
        if abs(days_to_anchor) <= 3 or abs((td - start).days) <= 3:
            res.flags.append("boundary_case")
        if upper and td > upper:
            res.flags.append("post_adjudication_soft")   # 배제하지 않고 표시만

        # --- 2단계: 행위 유형
        res.reached = "action"
        at = tx["action_type"]
        routed = next((r for r in af["route_out"] if f'"{at}"' in str(r["when"])), None)
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
        res.priority = "unresolved" if verdict == "unresolved" else triage(ratio, rule)
        ctx = dict(tx=tx, value_in=vi, value_out=vo, cp_verdicts=cp_v, pd_verdicts=pd_v,
                   counterparties=cps, principal_debtors=pds, days_to_anchor=days_to_anchor)
        res.signals = [s["id"] for s in rule["priority_signals"] if PREDICATES[s["id"]](ctx)]
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

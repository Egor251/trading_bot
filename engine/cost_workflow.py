"""Human-controlled workflow for AI-assisted trading-cost schedules.

AI may create :class:`CostScheduleDraft` values, but only this deterministic
module validates, stores, and explicitly activates them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from fnmatch import fnmatchcase
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from engine.costs import CostSchedule, TradeContext


def _now() -> datetime:
    return datetime.now(UTC)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class SourceDocument(StrictModel):
    document_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1)
    source_uri: str | None = None
    retrieved_at: datetime = Field(default_factory=_now)
    sha256: str = ""

    @model_validator(mode="after")
    def verify_hash(self) -> "SourceDocument":
        expected = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        if self.sha256 and self.sha256 != expected:
            raise ValueError("source sha256 does not match source text")
        if not self.sha256:
            object.__setattr__(self, "sha256", expected)
        return self


class SourceSpan(StrictModel):
    source_hash: str = Field(min_length=64, max_length=64)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)
    confidence: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))

    @model_validator(mode="after")
    def valid_range(self) -> "SourceSpan":
        if self.end <= self.start:
            raise ValueError("source span end must be after start")
        return self


class DraftCostMatch(StrictModel):
    venue: str | None = None
    account: str | None = None
    asset: str | None = None
    asset_class: str | None = None
    symbol: str = "*"
    side: Literal["buy", "sell"] | None = None
    position_effect: str | None = None
    order_type: str | None = None
    liquidity: Literal["maker", "taker"] | None = None
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    tier_min: Decimal | None = Field(default=None, ge=0)
    tier_max: Decimal | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def valid_ranges(self) -> "DraftCostMatch":
        if self.effective_from and self.effective_to and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must be after effective_from")
        if self.tier_min is not None and self.tier_max is not None and self.tier_max <= self.tier_min:
            raise ValueError("tier_max must be greater than tier_min")
        return self


CostKindValue = Literal[
    "bps", "fixed", "per_unit", "per_contract", "spread", "slippage", "funding", "borrow", "tax"
]


class DraftCostComponent(StrictModel):
    kind: CostKindValue
    rate: Decimal = Field(ge=0)
    name: str = Field(default="", max_length=200)
    minimum: Decimal | None = Field(default=None, ge=0)
    maximum: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def valid_range(self) -> "DraftCostComponent":
        if self.minimum is not None and self.maximum is not None and self.maximum < self.minimum:
            raise ValueError("maximum cannot be less than minimum")
        return self


class DraftCostRule(StrictModel):
    rule_id: str = Field(min_length=1, max_length=200)
    match: DraftCostMatch
    components: tuple[DraftCostComponent, ...] = Field(min_length=1)
    priority: int = 0
    source_spans: tuple[SourceSpan, ...] = Field(min_length=1)


class CostScheduleDraft(StrictModel):
    version: str = Field(min_length=1, max_length=200)
    rules: tuple[DraftCostRule, ...]
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    rationale: str = ""
    proposed_by: str = "ai"
    proposed_at: datetime = Field(default_factory=_now)

    @model_validator(mode="after")
    def valid_schedule(self) -> "CostScheduleDraft":
        ids = [rule.rule_id for rule in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("rule_id values must be unique")
        if self.effective_from and self.effective_to and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must be after effective_from")
        return self

    def schedule_dict(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        for rule in data["rules"]:
            rule.pop("source_spans", None)
        for key in ("rationale", "proposed_by", "proposed_at"):
            data.pop(key, None)
        return data

    def to_schedule(self) -> CostSchedule:
        return CostSchedule.from_dict(self.schedule_dict())

    @property
    def schedule_hash(self) -> str:
        return self.to_schedule().schedule_hash


class ValidationIssue(StrictModel):
    severity: Literal["error", "warning", "info"]
    code: str
    message: str
    rule_ids: tuple[str, ...] = ()


class GeneratedExample(StrictModel):
    label: str
    tier_value: str
    side: str
    liquidity: str
    matched_rules: tuple[str, ...]
    total: str


class ScheduleDiff(StrictModel):
    active_hash: str | None = None
    draft_hash: str
    added_rules: tuple[str, ...] = ()
    removed_rules: tuple[str, ...] = ()
    changed_rules: tuple[str, ...] = ()


class ImpactRow(StrictModel):
    context: dict[str, Any]
    active_total: str
    draft_total: str
    delta: str


class ValidationReport(StrictModel):
    valid: bool
    issues: tuple[ValidationIssue, ...]
    examples: tuple[GeneratedExample, ...]
    diff: ScheduleDiff
    impact: tuple[ImpactRow, ...]
    source_hash: str
    schedule_hash: str


def _grounding_issues(source: SourceDocument, draft: CostScheduleDraft) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for rule in draft.rules:
        for span in rule.source_spans:
            if span.source_hash != source.sha256:
                issues.append(ValidationIssue(
                    severity="error", code="source_hash_mismatch",
                    message=f"{rule.rule_id}: span refers to a different source hash",
                    rule_ids=(rule.rule_id,),
                ))
                continue
            if span.end > len(source.text):
                issues.append(ValidationIssue(
                    severity="error", code="source_span_out_of_range",
                    message=f"{rule.rule_id}: source span is outside the document",
                    rule_ids=(rule.rule_id,),
                ))
                continue
            if source.text[span.start:span.end] != span.quote:
                issues.append(ValidationIssue(
                    severity="error", code="source_quote_mismatch",
                    message=f"{rule.rule_id}: quote does not exactly match its source span",
                    rule_ids=(rule.rule_id,),
                ))
            if span.confidence < Decimal("0.5"):
                issues.append(ValidationIssue(
                    severity="warning", code="low_confidence",
                    message=f"{rule.rule_id}: source confidence is below 0.5",
                    rule_ids=(rule.rule_id,),
                ))
    return issues


def _rate_issues(draft: CostScheduleDraft) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for rule in draft.rules:
        for component in rule.components:
            if component.kind in {"bps", "spread", "slippage", "funding", "borrow", "tax"}:
                if component.rate > Decimal("10000"):
                    issues.append(ValidationIssue(
                        severity="error", code="rate_out_of_range",
                        message=f"{rule.rule_id}: {component.kind} rate exceeds 10000 bps",
                        rule_ids=(rule.rule_id,),
                    ))
            elif component.rate > Decimal("1000000000"):
                issues.append(ValidationIssue(
                    severity="error", code="rate_out_of_range",
                    message=f"{rule.rule_id}: rate is implausibly large",
                    rule_ids=(rule.rule_id,),
                ))
    return issues


_MATCH_FIELDS = (
    "venue", "account", "asset", "asset_class", "side", "position_effect", "order_type", "liquidity"
)


def _intervals_overlap(a_min: Decimal | None, a_max: Decimal | None, b_min: Decimal | None, b_max: Decimal | None) -> bool:
    lo_a, hi_a = a_min or Decimal("-Infinity"), a_max or Decimal("Infinity")
    lo_b, hi_b = b_min or Decimal("-Infinity"), b_max or Decimal("Infinity")
    return max(lo_a, lo_b) < min(hi_a, hi_b)


def _time_overlap(a: DraftCostMatch, b: DraftCostMatch) -> bool:
    lo_a = a.effective_from or datetime.min.replace(tzinfo=UTC)
    hi_a = a.effective_to or datetime.max.replace(tzinfo=UTC)
    lo_b = b.effective_from or datetime.min.replace(tzinfo=UTC)
    hi_b = b.effective_to or datetime.max.replace(tzinfo=UTC)
    return max(lo_a, lo_b) < min(hi_a, hi_b)


def _matches_overlap(a: DraftCostMatch, b: DraftCostMatch) -> bool:
    for name in _MATCH_FIELDS:
        av, bv = getattr(a, name), getattr(b, name)
        if av is not None and bv is not None and av.casefold() != bv.casefold():
            return False
    if not (fnmatchcase(a.symbol.casefold(), b.symbol.casefold()) or
            fnmatchcase(b.symbol.casefold(), a.symbol.casefold()) or
            a.symbol == "*" or b.symbol == "*"):
        return False
    return _intervals_overlap(a.tier_min, a.tier_max, b.tier_min, b.tier_max) and _time_overlap(a, b)


def _overlap_issues(draft: CostScheduleDraft) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    rules = list(draft.rules)
    for index, left in enumerate(rules):
        for right in rules[index + 1:]:
            if not _matches_overlap(left.match, right.match):
                continue
            duplicate = (
                left.match == right.match
                and left.components == right.components
                and left.priority == right.priority
            )
            issues.append(ValidationIssue(
                severity="error" if duplicate else "warning",
                code="duplicate_rule" if duplicate else "overlapping_rules",
                message=f"{left.rule_id} and {right.rule_id} have overlapping applicability",
                rule_ids=(left.rule_id, right.rule_id),
            ))
    return issues


def _coverage_issues(draft: CostScheduleDraft) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    sides = {r.match.side for r in draft.rules}
    liquidity = {r.match.liquidity for r in draft.rules}
    if None not in sides:
        missing = {"buy", "sell"} - sides
        if missing:
            issues.append(ValidationIssue(
                severity="warning", code="side_coverage",
                message=f"schedule does not cover side(s): {', '.join(sorted(missing))}",
            ))
    if None not in liquidity:
        missing = {"maker", "taker"} - liquidity
        if missing:
            issues.append(ValidationIssue(
                severity="warning", code="liquidity_coverage",
                message=f"schedule does not cover liquidity: {', '.join(sorted(missing))}",
            ))
    return issues


def _context(tier: Decimal, side: str, liquidity: str) -> TradeContext:
    return TradeContext(
        venue="validation", account="validation", asset="validation", asset_class="validation",
        symbol="VALIDATION", side=side, position_effect="open", order_type="market",
        liquidity=liquidity, trade_time=_now(), quantity=Decimal("1"), price=Decimal("100"),
        tier_value=max(Decimal("0"), tier), currency="USD",
    )


def _examples(schedule: CostSchedule, draft: CostScheduleDraft) -> tuple[GeneratedExample, ...]:
    boundaries = sorted({
        value for rule in draft.rules for value in (rule.match.tier_min, rule.match.tier_max)
        if value is not None
    })
    points: list[tuple[str, Decimal]] = [("base", Decimal("0"))]
    epsilon = Decimal("0.000001")
    for boundary in boundaries:
        points.extend([
            (f"below {boundary}", max(Decimal("0"), boundary - epsilon)),
            (f"at {boundary}", boundary),
            (f"above {boundary}", boundary + epsilon),
        ])
    out: list[GeneratedExample] = []
    for label, tier in points:
        for side in ("buy", "sell"):
            for liquidity in ("maker", "taker"):
                ctx = _context(tier, side, liquidity)
                result = schedule.calculate(ctx)
                out.append(GeneratedExample(
                    label=label, tier_value=str(tier), side=side, liquidity=liquidity,
                    matched_rules=tuple(line.rule_id for line in result.lines), total=str(result.total),
                ))
    return tuple(out)


def _diff(active: CostSchedule | None, draft: CostSchedule) -> ScheduleDiff:
    old = {rule.rule_id: rule.to_dict() for rule in active.rules} if active else {}
    new = {rule.rule_id: rule.to_dict() for rule in draft.rules}
    return ScheduleDiff(
        active_hash=active.schedule_hash if active else None,
        draft_hash=draft.schedule_hash,
        added_rules=tuple(sorted(new.keys() - old.keys())),
        removed_rules=tuple(sorted(old.keys() - new.keys())),
        changed_rules=tuple(sorted(key for key in new.keys() & old.keys() if new[key] != old[key])),
    )


def _impact(active: CostSchedule | None, draft: CostSchedule, contexts: Iterable[TradeContext | Mapping[str, Any]]) -> tuple[ImpactRow, ...]:
    rows: list[ImpactRow] = []
    for raw in contexts:
        ctx = raw if isinstance(raw, TradeContext) else TradeContext.from_dict(raw)
        new_total = draft.calculate(ctx).total
        old_total = active.calculate(ctx).total if active else Decimal("0")
        rows.append(ImpactRow(
            context=ctx.to_dict(), active_total=str(old_total), draft_total=str(new_total),
            delta=str(new_total - old_total),
        ))
    return tuple(rows)


def validate_cost_draft(
    source: SourceDocument | Mapping[str, Any],
    draft: CostScheduleDraft | Mapping[str, Any],
    *,
    active: CostSchedule | Mapping[str, Any] | None = None,
    impact_contexts: Iterable[TradeContext | Mapping[str, Any]] = (),
) -> ValidationReport:
    """Deterministically validate an AI/operator proposal; performs no writes."""
    source_obj = source if isinstance(source, SourceDocument) else SourceDocument.model_validate(source)
    draft_obj = draft if isinstance(draft, CostScheduleDraft) else CostScheduleDraft.model_validate(draft)
    active_obj = active
    if isinstance(active, Mapping):
        active_obj = CostSchedule.from_dict(active)
    schedule = draft_obj.to_schedule()
    issues = [
        *_grounding_issues(source_obj, draft_obj),
        *_rate_issues(draft_obj),
        *_overlap_issues(draft_obj),
        *_coverage_issues(draft_obj),
    ]
    return ValidationReport(
        valid=not any(issue.severity == "error" for issue in issues),
        issues=tuple(issues),
        examples=_examples(schedule, draft_obj),
        diff=_diff(active_obj, schedule),
        impact=_impact(active_obj, schedule, impact_contexts),
        source_hash=source_obj.sha256,
        schedule_hash=schedule.schedule_hash,
    )


class StoredDraft(StrictModel):
    draft_hash: str
    schedule_hash: str
    source: SourceDocument
    draft: CostScheduleDraft
    validation: ValidationReport
    created_at: datetime
    created_by: str


class ActivationRecord(StrictModel):
    draft_hash: str
    schedule_hash: str
    activated_at: datetime
    activated_by: str
    previous_draft_hash: str | None = None


class CostScheduleStore:
    """Immutable content-addressed drafts plus an explicit active pointer."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.drafts_dir = self.root / "drafts"
        self.activations_dir = self.root / "activations"
        self.active_path = self.root / "active.json"

    def _ensure(self) -> None:
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        self.activations_dir.mkdir(parents=True, exist_ok=True)

    def save_draft(
        self,
        source: SourceDocument | Mapping[str, Any],
        draft: CostScheduleDraft | Mapping[str, Any],
        *,
        created_by: str,
        active: CostSchedule | None = None,
        impact_contexts: Iterable[TradeContext | Mapping[str, Any]] = (),
    ) -> StoredDraft:
        source_obj = source if isinstance(source, SourceDocument) else SourceDocument.model_validate(source)
        draft_obj = draft if isinstance(draft, CostScheduleDraft) else CostScheduleDraft.model_validate(draft)
        report = validate_cost_draft(
            source_obj, draft_obj, active=active or self.load_active_schedule(),
            impact_contexts=impact_contexts,
        )
        if not report.valid:
            raise ValueError("invalid cost draft cannot be saved")
        created_at = _now()
        body = {
            "source": source_obj.model_dump(mode="json"),
            "draft": draft_obj.model_dump(mode="json"),
            "validation": report.model_dump(mode="json"),
            "created_at": created_at.isoformat(),
            "created_by": str(created_by or "unknown"),
        }
        draft_hash = _hash(body)
        record = StoredDraft(
            draft_hash=draft_hash, schedule_hash=report.schedule_hash,
            source=source_obj, draft=draft_obj, validation=report,
            created_at=created_at, created_by=str(created_by or "unknown"),
        )
        self._ensure()
        path = self.drafts_dir / f"{draft_hash}.json"
        data = _canonical(record.model_dump(mode="json"))
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError as exc:
            existing = path.read_text(encoding="utf-8")
            if existing != data:
                # A truncated file left by a crash mid-write poisons this
                # content-addressed path permanently: every later save for the
                # same hash raises. Surface the cause so it is diagnosable.
                raise RuntimeError("immutable draft hash collision") from exc
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        return record

    def load_draft(self, draft_hash: str) -> StoredDraft:
        if len(draft_hash) != 64:
            raise ValueError("invalid draft hash")
        return StoredDraft.model_validate_json(
            (self.drafts_dir / f"{draft_hash}.json").read_text(encoding="utf-8")
        )

    def active_record(self) -> ActivationRecord | None:
        if not self.active_path.is_file():
            return None
        return ActivationRecord.model_validate_json(self.active_path.read_text(encoding="utf-8"))

    def load_active_schedule(self) -> CostSchedule | None:
        active = self.active_record()
        return None if active is None else self.load_draft(active.draft_hash).draft.to_schedule()

    def write_runtime_effective(self, schedule: CostSchedule) -> Path:
        """Persist the schedule actually used by TradingRuntime (incl. legacy-bps).

        This does not activate a draft — it only makes the effective schedule
        inspectable when ``active.json`` is empty.
        """
        if not isinstance(schedule, CostSchedule):
            raise TypeError("schedule must be a CostSchedule")
        self._ensure()
        path = self.root / "runtime_effective.json"
        temporary = self.root / f".runtime_effective-{os.getpid()}.tmp"
        temporary.write_text(schedule.to_json(), encoding="utf-8")
        os.replace(temporary, path)
        return path

    def load_runtime_effective(self) -> CostSchedule | None:
        path = self.root / "runtime_effective.json"
        if not path.is_file():
            return None
        try:
            return CostSchedule.from_json(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def activate(self, expected_hash: str, *, activated_by: str) -> ActivationRecord:
        """Revalidate stored bytes and atomically point active.json at exactly that hash."""
        stored = self.load_draft(expected_hash)
        body = {
            "source": stored.source.model_dump(mode="json"),
            "draft": stored.draft.model_dump(mode="json"),
            "validation": stored.validation.model_dump(mode="json"),
            "created_at": stored.created_at.isoformat(),
            "created_by": stored.created_by,
        }
        if _hash(body) != expected_hash or stored.draft_hash != expected_hash:
            raise ValueError("draft hash mismatch")
        report = validate_cost_draft(
            stored.source, stored.draft, active=self.load_active_schedule()
        )
        if not report.valid or report.schedule_hash != stored.schedule_hash:
            raise ValueError("server-side cost draft revalidation failed")
        previous = self.active_record()
        record = ActivationRecord(
            draft_hash=expected_hash, schedule_hash=stored.schedule_hash,
            activated_at=_now(), activated_by=str(activated_by or "unknown"),
            previous_draft_hash=previous.draft_hash if previous else None,
        )
        self._ensure()
        audit_path = self.activations_dir / f"{record.activated_at.strftime('%Y%m%dT%H%M%S%fZ')}-{expected_hash}.json"
        audit_path.write_text(_canonical(record.model_dump(mode="json")), encoding="utf-8")
        temporary = self.root / f".active-{os.getpid()}.tmp"
        temporary.write_text(_canonical(record.model_dump(mode="json")), encoding="utf-8")
        os.replace(temporary, self.active_path)
        return record


# Friendly compatibility names for callers.
GroundedSourceSpan = SourceSpan
DraftRule = DraftCostRule
DraftSchedule = CostScheduleDraft
validate_draft = validate_cost_draft
VersionedCostStore = CostScheduleStore


__all__ = [
    "ActivationRecord", "CostScheduleDraft", "CostScheduleStore", "DraftCostComponent",
    "DraftCostMatch", "DraftCostRule", "DraftRule", "DraftSchedule", "GeneratedExample",
    "GroundedSourceSpan", "ImpactRow", "ScheduleDiff", "SourceDocument", "SourceSpan",
    "StoredDraft", "ValidationIssue", "ValidationReport", "VersionedCostStore",
    "validate_cost_draft", "validate_draft",
]

from collections import Counter

from .log import AuditLevel, AuditLog
from .risk import RiskAnalyzer


class AuditReports:
    def __init__(self, audit_log: AuditLog, risk_analyzer: RiskAnalyzer):
        self.audit_log = audit_log
        self.risk_analyzer = risk_analyzer

    def suspicious_operations(self, include_medium: bool = True) -> list[dict]:
        """Одна строка на риск-оценку (MEDIUM/HIGH), включая отклонённые."""
        allowed = {AuditLevel.HIGH, AuditLevel.MEDIUM} if include_medium else {AuditLevel.HIGH}
        result = [event.as_dict() for event in self.audit_log.filter(event_type="RISK_ASSESSED")
                  if event.level in allowed]
        return result

    def client_risk_profile(self, client_id: str) -> dict:
        profile = self.risk_analyzer.client_profile(client_id)
        events = self.audit_log.filter(client_id=client_id)
        profile["blocked_operations"] = sum(e.event_type == "TRANSACTION_BLOCKED" for e in events)
        profile["failed_operations"] = sum(e.event_type == "TRANSACTION_FAILED" for e in events)
        return profile

    def error_statistics(self) -> dict:
        entries = self.audit_log.entries
        errors = [e for e in entries if e.event_type in (
            "TRANSACTION_FAILED", "TRANSACTION_RETRY", "TRANSACTION_BLOCKED")]
        return {
            "total_error_events": len(errors),
            "failed": sum(e.event_type == "TRANSACTION_FAILED" for e in errors),
            "retries": sum(e.event_type == "TRANSACTION_RETRY" for e in errors),
            "blocked": sum(e.event_type == "TRANSACTION_BLOCKED" for e in errors),
            "by_error_type": dict(Counter(e.error_type or "Unknown" for e in errors)),
        }

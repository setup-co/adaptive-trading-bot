"""Transport models for the read-only decision and completed-trade APIs."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from adaptive_trading_bot.dashboard.journal_models import (
    DashboardCompletedTradeRecord,
    DashboardDecisionPage,
    DashboardDecisionRecord,
    DashboardTradePage,
)


class DashboardJournalApiModel(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)


class DashboardDecisionPageResponse(DashboardJournalApiModel):
    start: datetime
    end: datetime
    limit: int
    total_matched: int
    returned_count: int
    truncated: bool
    records: tuple[DashboardDecisionRecord, ...]


class DashboardTradePageResponse(DashboardJournalApiModel):
    start: datetime
    end: datetime
    limit: int
    total_matched: int
    returned_count: int
    truncated: bool
    records: tuple[DashboardCompletedTradeRecord, ...]


class DashboardJournalInvalidQueryResponse(DashboardJournalApiModel):
    status: Literal["invalid_request"] = "invalid_request"
    code: Literal["DASHBOARD_JOURNAL_INVALID_QUERY"] = "DASHBOARD_JOURNAL_INVALID_QUERY"
    message: Literal["dashboard journal query is invalid"] = "dashboard journal query is invalid"


def decision_page_response(page: DashboardDecisionPage) -> DashboardDecisionPageResponse:
    return DashboardDecisionPageResponse.model_validate(page)


def trade_page_response(page: DashboardTradePage) -> DashboardTradePageResponse:
    return DashboardTradePageResponse.model_validate(page)

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from chainalpha.five_good.models import (
    EntryStatus,
    FiveGoodCategory,
    FiveGoodHistory,
    FiveGoodListResponse,
    FiveGoodOverview,
    FiveGoodStockDetail,
)
from chainalpha.five_good.store import five_good_store


router = APIRouter(prefix="/api/five-good", tags=["five-good"])


@router.get("/overview", response_model=FiveGoodOverview)
def five_good_overview(limit: int = Query(6, ge=3, le=10)) -> FiveGoodOverview:
    return five_good_store.overview(limit=limit)


@router.get("/stocks", response_model=FiveGoodListResponse)
def five_good_stocks(
    category: FiveGoodCategory | None = None,
    keyword: str = Query("", max_length=80),
    market: str = Query("", max_length=12),
    industry: str = Query("", max_length=60),
    sector: str = Query("", max_length=60),
    status: EntryStatus | None = None,
    updated_within: str = Query("all", pattern="^(today|3d|7d|all)$"),
    sort: str = Query("score_desc", pattern="^(score_desc|updated_desc|updated_asc)$"),
    cursor: str | None = Query(None, max_length=80),
    limit: int = Query(30, ge=1, le=100),
) -> FiveGoodListResponse:
    try:
        return five_good_store.list_entries(
            category=category,
            keyword=keyword,
            market=market,
            industry=industry,
            sector=sector,
            status=status,
            updated_within=updated_within,
            sort=sort,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.get("/stocks/{stock_code}/history", response_model=list[FiveGoodHistory])
def five_good_history(stock_code: str) -> list[FiveGoodHistory]:
    return five_good_store.stock_history(stock_code)


@router.get("/stocks/{stock_code}", response_model=FiveGoodStockDetail)
def five_good_detail(stock_code: str) -> FiveGoodStockDetail:
    detail = five_good_store.stock_detail(stock_code)
    if detail is None:
        raise HTTPException(status_code=404, detail="Stock not found in Five Good pools")
    return detail

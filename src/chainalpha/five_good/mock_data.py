from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from chainalpha.five_good.models import (
    EntryStatus,
    FiveGoodCategory,
    FiveGoodEntry,
    FiveGoodHistory,
    HistoryAction,
)


STOCKS = [
    ("600519.SH", "贵州茅台", "sh", "食品饮料", "白酒", "消费"),
    ("600036.SH", "招商银行", "sh", "银行", "股份制银行", "金融"),
    ("601318.SH", "中国平安", "sh", "非银金融", "保险", "金融"),
    ("000333.SZ", "美的集团", "sz", "家用电器", "白色家电", "消费"),
    ("300750.SZ", "宁德时代", "sz", "电力设备", "动力电池", "新能源车"),
    ("002594.SZ", "比亚迪", "sz", "汽车", "新能源整车", "新能源车"),
    ("300308.SZ", "中际旭创", "sz", "通信", "光模块", "AI算力"),
    ("300502.SZ", "新易盛", "sz", "通信", "光模块", "AI算力"),
    ("688041.SH", "海光信息", "sh", "半导体", "CPU/GPU", "国产算力"),
    ("603986.SH", "兆易创新", "sh", "半导体", "存储芯片", "国产替代"),
    ("002463.SZ", "沪电股份", "sz", "电子", "PCB", "AI算力"),
    ("688017.SH", "绿的谐波", "sh", "机械设备", "减速器", "机器人"),
    ("600900.SH", "长江电力", "sh", "公用事业", "水电", "高股息"),
    ("601088.SH", "中国神华", "sh", "煤炭", "动力煤", "高股息"),
    ("920106.BJ", "林泰新材", "bj", "汽车零部件", "传动系统", "北交所"),
]


def build_demo_entries(now: datetime | None = None) -> tuple[list[FiveGoodEntry], list[FiveGoodHistory]]:
    now = now or datetime.now(timezone.utc)
    plan: dict[FiveGoodCategory, list[int]] = {
        FiveGoodCategory.GOOD_COMPANY: [0, 1, 2, 3, 4, 12, 13],
        FiveGoodCategory.GOOD_TIMING: [1, 3, 6, 7, 9, 10, 14],
        FiveGoodCategory.GOOD_PRICE: [0, 1, 2, 3, 9, 12, 13],
        FiveGoodCategory.GOOD_STORY: [4, 5, 6, 8, 9, 11],
        FiveGoodCategory.GOOD_STOCK: [4, 6, 7, 8, 10, 11, 14],
    }
    entries: list[FiveGoodEntry] = []
    history: list[FiveGoodHistory] = []
    for category, indexes in plan.items():
        for order, stock_index in enumerate(indexes):
            code, name, market, industry, sub_industry, sector = STOCKS[stock_index]
            score = min(96, 91 - order * 2 + stock_index % 4)
            entry_id = f"demo-{category.value}-{code.lower().replace('.', '-')}"
            updated = now - timedelta(hours=(order * 7 + stock_index) % 120)
            entry = FiveGoodEntry(
                id=entry_id,
                stock_code=code,
                stock_name=name,
                market=market,
                category=category,
                industry=industry,
                sub_industry=sub_industry,
                sector=sector,
                score=score,
                status=EntryStatus.ACTIVE if order < 5 else EntryStatus.WATCHING,
                selection_reason=_reason(category, name, sub_industry),
                risk_summary=_risk(category, industry),
                tags=_tags(category, stock_index),
                valid_from=updated - timedelta(days=20 + order),
                valid_until=(updated + timedelta(days=7)) if category == FiveGoodCategory.GOOD_TIMING else None,
                as_of_date=updated.date(),
                created_at=updated - timedelta(days=20 + order),
                updated_at=updated,
                category_data=_category_data(category, score, stock_index, sector, updated),
            )
            entries.append(entry)
            history.append(
                FiveGoodHistory(
                    id=f"history-{entry_id}",
                    entry_id=entry_id,
                    stock_code=code,
                    category=category,
                    action=HistoryAction.ADDED,
                    new_score=score,
                    reason=entry.selection_reason,
                    as_of_date=entry.valid_from.date(),
                    created_at=entry.valid_from,
                )
            )
    return entries, history


def _reason(category: FiveGoodCategory, name: str, sub_industry: str) -> str:
    reasons = {
        FiveGoodCategory.GOOD_COMPANY: f"{name}在{sub_industry}领域具备较强市场地位，盈利与现金流质量值得长期跟踪。",
        FiveGoodCategory.GOOD_TIMING: "价格回到前期震荡区间附近，量价和资金信号处于观察阶段。",
        FiveGoodCategory.GOOD_PRICE: "当前估值相对自身历史区间有所回落，仍需结合盈利兑现持续验证。",
        FiveGoodCategory.GOOD_STORY: "近期产业或公司事件可能改变市场关注度，催化仍需公告与后续数据确认。",
        FiveGoodCategory.GOOD_STOCK: f"位于{sub_industry}核心环节，对产业景气和主题交易具有较高弹性。",
    }
    return reasons[category]


def _risk(category: FiveGoodCategory, industry: str) -> str:
    common = f"需关注{industry}景气变化、盈利兑现和市场波动。"
    if category == FiveGoodCategory.GOOD_TIMING:
        return "技术形态可能失效，资金转弱后观察区间需要重新计算。"
    if category == FiveGoodCategory.GOOD_STORY:
        return "事件可能低于预期、被证伪或已经被市场提前定价。"
    return common


def _tags(category: FiveGoodCategory, index: int) -> list[str]:
    labels = {
        FiveGoodCategory.GOOD_COMPANY: ["行业龙头", "盈利稳定", "现金流关注"],
        FiveGoodCategory.GOOD_TIMING: ["区间观察", "量价跟踪", "非交易指令"],
        FiveGoodCategory.GOOD_PRICE: ["估值回落", "盈利验证", "历史分位"],
        FiveGoodCategory.GOOD_STORY: ["事件催化", "持续跟踪", "待交叉验证"],
        FiveGoodCategory.GOOD_STOCK: ["产业弹性", "热点关联", "波段跟踪"],
    }
    return labels[category][: 2 + index % 2]


def _category_data(
    category: FiveGoodCategory,
    score: float,
    index: int,
    sector: str,
    updated: datetime,
) -> dict[str, Any]:
    price = round(18 + index * 13.7, 2)
    if category == FiveGoodCategory.GOOD_COMPANY:
        return {
            "industry_position": "细分行业龙头" if index % 2 == 0 else "行业第一梯队",
            "roe": round(12.5 + index % 6 * 2.1, 2),
            "gross_margin": round(24 + index % 5 * 5.2, 2),
            "cashflow_quality": "优秀" if index % 3 else "稳健",
            "dividend_yield": round(1.4 + index % 5 * 0.8, 2),
            "profit_growth": "稳定增长",
            "competitive_advantage": "品牌、规模和客户壁垒",
        }
    if category == FiveGoodCategory.GOOD_TIMING:
        return {
            "current_price": price,
            "box_low": round(price * 0.91, 2),
            "box_high": round(price * 1.12, 2),
            "watch_zone_low": round(price * 0.95, 2),
            "watch_zone_high": round(price * 1.02, 2),
            "support_price": round(price * 0.9, 2),
            "invalid_price": round(price * 0.87, 2),
            "technical_signal": ["回踩", "震荡", "突破观察"][index % 3],
            "capital_flow_signal": ["流入", "平稳", "数据不足"][index % 3],
        }
    if category == FiveGoodCategory.GOOD_PRICE:
        return {
            "current_price": price,
            "pe": round(10 + index * 2.7, 2),
            "pb": round(1.1 + index * 0.28, 2),
            "peg": round(0.72 + index * 0.09, 2),
            "historical_pe_percentile": 18 + index * 4,
            "profit_growth_expectation": f"{12 + index % 7 * 3}%",
            "valuation_status": ["偏低", "合理", "数据不足"][index % 3],
            "industry_valuation_comparison": "低于行业中位数" if index % 2 else "接近行业中位数",
        }
    if category == FiveGoodCategory.GOOD_STORY:
        return {
            "event_id": "",
            "event_title": "产业催化与公司进展跟踪",
            "event_type": "产业催化",
            "event_score": score,
            "event_status": "tracking",
            "impact_direction": "positive" if index % 2 else "mixed",
            "confidence": 62 + index % 5 * 5,
            "event_occurred_at": updated.isoformat(),
            "catalyst_summary": "关注事件后续公告、订单与财务兑现。",
        }
    return {
        "industry_chain": sector,
        "chain_position": STOCKS[index][4],
        "elasticity_score": score,
        "theme_relevance_score": min(96, score + 3),
        "relative_strength": ["强", "中等", "待验证"][index % 3],
        "market_cap_level": ["大盘", "中盘", "小盘"][index % 3],
        "catalyst_summary": "产业景气、订单和板块热度共同驱动。",
    }

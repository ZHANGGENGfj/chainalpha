from __future__ import annotations

from typing import Any


def build_target_valuation_view(companies: list[Any], candidates: list[Any]) -> list[str]:
    """Build a target-company-first valuation view.

    This remains useful for news-only runs: it clearly separates evidence from
    data gaps instead of pretending valuation can be concluded without market
    data, multiples, and forecasts.
    """
    items: list[str] = []
    focus_rows = companies[:3]
    for row in focus_rows:
        financial = getattr(row, "financial", None)
        price = getattr(row, "price", None)
        company = getattr(row, "company", None)
        name = getattr(company, "name", "目标公司")
        items.append(f"{name}: 估值分析聚焦目标公司，不再把产业链相关公司简单并列为投资候选。")

        facts: list[str] = []
        if financial:
            if getattr(financial, "period", None):
                facts.append(f"报告期={financial.period}")
            if getattr(financial, "revenue_growth", None) is not None:
                facts.append(f"营收增速={financial.revenue_growth:.1f}%")
            if getattr(financial, "profit_growth", None) is not None:
                facts.append(f"利润增速={financial.profit_growth:.1f}%")
            if getattr(financial, "gross_margin", None) is not None:
                facts.append(f"毛利率={financial.gross_margin:.1f}%")
            if getattr(financial, "roe", None) is not None:
                facts.append(f"ROE={financial.roe:.1f}%")
            if getattr(financial, "market_cap", None) is not None:
                facts.append(f"市值={financial.market_cap:.1f}")
            valuation_parts = []
            if getattr(financial, "pe", None) is not None:
                valuation_parts.append(f"PE={financial.pe:.1f}")
            if getattr(financial, "pb", None) is not None:
                valuation_parts.append(f"PB={financial.pb:.1f}")
            if getattr(financial, "ps", None) is not None:
                valuation_parts.append(f"PS={financial.ps:.1f}")
            facts.append("估值倍数=" + (", ".join(valuation_parts) if valuation_parts else "缺失"))
        else:
            facts.append("结构化财务数据缺失")

        if price:
            price_parts = []
            if getattr(price, "date", None):
                price_parts.append(str(price.date))
            if getattr(price, "close", None) is not None:
                price_parts.append(f"收盘价={price.close:.2f}")
            if getattr(price, "change_1m", None) is not None:
                price_parts.append(f"1M={price.change_1m:.1f}%")
            if getattr(price, "change_3m", None) is not None:
                price_parts.append(f"3M={price.change_3m:.1f}%")
            if getattr(price, "change_6m", None) is not None:
                price_parts.append(f"6M={price.change_6m:.1f}%")
            if getattr(price, "change_12m", None) is not None:
                price_parts.append(f"12M={price.change_12m:.1f}%")
            facts.append("行情=" + (", ".join(price_parts) if price_parts else "缺失"))
        else:
            facts.append("结构化行情数据缺失")

        items.append(f"{name}: {'；'.join(facts)}。")
        if financial is None and price is None:
            items.extend(
                [
                    "当前仅启用新闻/消息输入，不能直接判断估值便宜或昂贵；只能判断消息是否可能改变盈利预期或风险偏好。",
                    "估值数据缺口：当前市值、PE TTM、PB、PS、过去3-5年估值分位、未来2-3年利润预测、同行估值表。",
                    "消息驱动估值框架：订单/客户认证/产品放量/毛利改善属于盈利上修线索；单纯热度新闻属于情绪催化，不能直接外推估值空间。",
                    "情景估值框架：乐观情景=订单持续兑现且估值维持；中性情景=业绩正常增长但估值回归中枢；悲观情景=订单或毛利不及预期导致业绩和估值双杀。",
                ]
            )
        else:
            items.append(
                f"{name}: 下一步需要用历史估值分位、同行估值、PEG和三年盈利预测来验证当前定价是否已经充分反映新闻利好。"
            )

    if candidates:
        top = candidates[0]
        top_company = getattr(top, "company", None)
        top_name = getattr(top_company, "name", "")
        focus_names = {getattr(getattr(row, "company", None), "name", "") for row in focus_rows}
        if top_name in focus_names:
            items.append(f"{top_name}: 候选池评分仅作为辅助，仍需用目标公司估值倍数、盈利预测和新闻证据闭环复核。")

    if not items:
        items.append("估值信息缺失：未识别到目标公司。请先确认公司名称/股票代码，并补充新闻、行情和估值数据。")
    return items

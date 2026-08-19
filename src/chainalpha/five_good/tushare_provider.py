from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any

from chainalpha.five_good.screening import StockFeatures, calculate_technical_features


class TushareScreeningProvider:
    """Collect a full A-share cross section and cache normalized screening features."""

    def __init__(
        self,
        *,
        token: str | None = None,
        base_url: str | None = None,
        cache_path: str | Path = "data/five_good/features.json",
        price_days: int = 70,
    ) -> None:
        self.token = token or os.getenv("TUSHARE_TOKEN", "")
        self.base_url = base_url or os.getenv("TUSHARE_BASE_URL", "") or os.getenv("TUSHARE_API_URL", "")
        self.cache_path = Path(cache_path)
        self.price_days = max(61, price_days)
        self.warnings: list[str] = []

    def collect(self, *, refresh: bool = False) -> list[StockFeatures]:
        if self.cache_path.exists() and not refresh:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return [StockFeatures.from_dict(item) for item in payload.get("features", [])]
        pro = self._client()
        universe = self._records(pro.stock_basic(
            exchange="",
            list_status="L",
            fields="ts_code,symbol,name,area,industry,market,exchange,list_date",
        ))
        if not universe:
            raise RuntimeError("Tushare stock_basic returned no active A-share companies")
        today = date.today()
        trade_dates = self._trade_dates(pro, today)
        if not trade_dates:
            raise RuntimeError("Tushare trade_cal returned no open trading dates")
        latest_date = trade_dates[-1]
        daily_basic = self._index(self._records(pro.daily_basic(
            trade_date=latest_date,
            fields=(
                "ts_code,trade_date,close,turnover_rate,volume_ratio,pe_ttm,pb,ps_ttm,"
                "dv_ttm,total_mv,circ_mv"
            ),
        )))
        financial = self._financial_indicators(pro, today)
        price_history = self._price_history(pro, trade_dates)
        fund_flow = self._fund_flow(pro, trade_dates[-5:])
        industry_returns = self._industry_returns(universe, price_history)
        financial_coverage = len(financial) / len(universe)
        market_coverage = len(daily_basic) / len(universe)
        history_coverage = sum(len(rows) >= 61 for rows in price_history.values()) / len(universe)
        if financial_coverage < 0.50:
            raise RuntimeError(
                f"Financial coverage is only {financial_coverage:.1%}; fina_indicator_vip permission or upstream data is incomplete"
            )
        if market_coverage < 0.80 or history_coverage < 0.60:
            raise RuntimeError(
                f"Market coverage is incomplete: daily_basic={market_coverage:.1%}, 61-day history={history_coverage:.1%}"
            )

        features: list[StockFeatures] = []
        for stock in universe:
            code = str(stock.get("ts_code") or "")
            basic = daily_basic.get(code, {})
            fin = financial.get(code, {})
            history = price_history.get(code, [])
            closes = [self._float(item.get("close")) for item in history]
            volumes = [self._float(item.get("vol")) for item in history]
            closes = [value for value in closes if value is not None]
            volumes = [value or 0 for value in volumes]
            technical = calculate_technical_features(closes, volumes)
            market = _market_code(code)
            industry = str(stock.get("industry") or "未分类")
            features.append(StockFeatures(
                stock_code=code,
                stock_name=str(stock.get("name") or ""),
                market=market,
                industry=industry,
                sub_industry=industry,
                sector=industry,
                list_date=str(stock.get("list_date") or ""),
                report_period=str(fin.get("end_date") or ""),
                announcement_date=str(fin.get("ann_date") or ""),
                as_of_date=latest_date,
                roe=self._float(fin.get("roe")),
                gross_margin=self._float(fin.get("grossprofit_margin")),
                net_margin=self._float(fin.get("netprofit_margin")),
                revenue_growth=self._first_float(fin, "q_sales_yoy", "tr_yoy", "or_yoy"),
                profit_growth=self._first_float(fin, "q_netprofit_yoy", "q_profit_yoy", "netprofit_yoy"),
                debt_ratio=self._float(fin.get("debt_to_assets")),
                cashflow_to_revenue=self._first_float(fin, "ocf_to_or", "ocf_to_revenue"),
                current_ratio=self._float(fin.get("current_ratio")),
                pe_ttm=self._float(basic.get("pe_ttm")),
                pb=self._float(basic.get("pb")),
                ps_ttm=self._float(basic.get("ps_ttm")),
                dividend_yield=self._float(basic.get("dv_ttm")),
                market_cap=self._float(basic.get("total_mv")),
                close=self._float(basic.get("close")),
                turnover_rate=self._float(basic.get("turnover_rate")),
                volume_ratio=self._float(basic.get("volume_ratio")) or technical.get("volume_ratio"),
                return_20d=technical.get("return_20d"),
                return_60d=technical.get("return_60d"),
                price_vs_ma20=technical.get("price_vs_ma20"),
                ma20_vs_ma60=technical.get("ma20_vs_ma60"),
                volatility_20d=technical.get("volatility_20d"),
                fund_flow_5d=fund_flow.get(code),
                industry_momentum=industry_returns.get(industry),
                sources=["tushare:stock_basic", "tushare:fina_indicator", "tushare:daily_basic", "tushare:daily"],
            ))
        self._save(features, latest_date, len(universe))
        return features

    def _client(self):
        if not self.token:
            raise RuntimeError("TUSHARE_TOKEN is not configured")
        try:
            import tushare as ts
        except ImportError as exc:
            raise RuntimeError('tushare is not installed; run: pip install "tushare>=1.4.0"') from exc
        pro = ts.pro_api(self.token)
        if self.base_url:
            pro._DataApi__http_url = self.base_url
        return pro

    def _trade_dates(self, pro, today: date) -> list[str]:
        start = today - timedelta(days=self.price_days * 2 + 30)
        rows = self._records(pro.trade_cal(
            exchange="SSE",
            start_date=start.strftime("%Y%m%d"),
            end_date=today.strftime("%Y%m%d"),
            is_open="1",
            fields="cal_date,is_open",
        ))
        return sorted(str(item.get("cal_date")) for item in rows if str(item.get("is_open")) in {"1", "1.0"})[-self.price_days:]

    def _financial_indicators(self, pro, today: date) -> dict[str, dict[str, Any]]:
        output: dict[str, dict[str, Any]] = {}
        fields = (
            "ts_code,ann_date,end_date,roe,grossprofit_margin,netprofit_margin,q_sales_yoy,"
            "tr_yoy,or_yoy,q_netprofit_yoy,q_profit_yoy,netprofit_yoy,debt_to_assets,ocf_to_or,current_ratio"
        )
        for period in _recent_quarter_ends(today, count=8):
            rows: list[dict[str, Any]] = []
            try:
                rows = self._records(pro.fina_indicator_vip(period=period, fields=fields))
            except Exception as exc:
                self.warnings.append(f"fina_indicator_vip {period} failed: {type(exc).__name__}: {exc}")
                try:
                    rows = self._records(pro.fina_indicator(period=period, fields=fields))
                except Exception as fallback_exc:
                    self.warnings.append(f"fina_indicator {period} failed: {type(fallback_exc).__name__}: {fallback_exc}")
            for row in rows:
                code = str(row.get("ts_code") or "")
                current_key = (str(row.get("end_date") or ""), str(row.get("ann_date") or ""))
                previous_key = (
                    str(output.get(code, {}).get("end_date") or ""),
                    str(output.get(code, {}).get("ann_date") or ""),
                )
                if code and (code not in output or current_key > previous_key):
                    output[code] = row
        if len(output) < 1000:
            self.warnings.append(
                f"Only {len(output)} companies have financial indicators; full-market fina_indicator_vip permission may be missing"
            )
        return output

    def _price_history(self, pro, trade_dates: list[str]) -> dict[str, list[dict[str, Any]]]:
        output: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for trade_date in trade_dates:
            try:
                rows = self._records(pro.daily(
                    trade_date=trade_date,
                    fields="ts_code,trade_date,close,vol,amount,pct_chg",
                ))
            except Exception as exc:
                self.warnings.append(f"daily {trade_date} failed: {type(exc).__name__}: {exc}")
                continue
            for row in rows:
                output[str(row.get("ts_code") or "")].append(row)
        for rows in output.values():
            rows.sort(key=lambda item: str(item.get("trade_date") or ""))
        return output

    def _fund_flow(self, pro, trade_dates: list[str]) -> dict[str, float]:
        output: dict[str, float] = defaultdict(float)
        for trade_date in trade_dates:
            try:
                rows = self._records(pro.moneyflow(
                    trade_date=trade_date,
                    fields="ts_code,trade_date,net_mf_amount",
                ))
            except Exception as exc:
                self.warnings.append(f"moneyflow {trade_date} failed: {type(exc).__name__}: {exc}")
                continue
            for row in rows:
                output[str(row.get("ts_code") or "")] += self._float(row.get("net_mf_amount")) or 0
        return dict(output)

    def _industry_returns(
        self,
        universe: list[dict[str, Any]],
        histories: dict[str, list[dict[str, Any]]],
    ) -> dict[str, float]:
        code_industry = {str(item.get("ts_code") or ""): str(item.get("industry") or "未分类") for item in universe}
        values: dict[str, list[float]] = defaultdict(list)
        for code, rows in histories.items():
            closes = [self._float(item.get("close")) for item in rows]
            closes = [value for value in closes if value is not None]
            if len(closes) >= 21 and closes[-21]:
                values[code_industry.get(code, "未分类")].append((closes[-1] / closes[-21] - 1) * 100)
        return {industry: sum(rows) / len(rows) for industry, rows in values.items() if rows}

    def _save(self, features: list[StockFeatures], trade_date: str, universe_count: int) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "metadata": {
                "provider": "tushare",
                "trade_date": trade_date,
                "universe_count": universe_count,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "warnings": self.warnings,
            },
            "features": [item.to_dict() for item in features],
        }
        temporary = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        temporary.replace(self.cache_path)

    @staticmethod
    def _records(frame: Any) -> list[dict[str, Any]]:
        if frame is None or getattr(frame, "empty", False):
            return []
        if hasattr(frame, "to_dict"):
            return frame.to_dict(orient="records")
        return list(frame) if isinstance(frame, (list, tuple)) else []

    @staticmethod
    def _index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {str(item.get("ts_code") or ""): item for item in rows if item.get("ts_code")}

    @staticmethod
    def _float(value: Any) -> float | None:
        try:
            number = float(value)
            return number if number == number else None
        except (TypeError, ValueError):
            return None

    def _first_float(self, row: dict[str, Any], *keys: str) -> float | None:
        for key in keys:
            value = self._float(row.get(key))
            if value is not None:
                return value
        return None


def _recent_quarter_ends(today: date, *, count: int) -> list[str]:
    periods = []
    year = today.year
    quarter_ends = [(12, 31), (9, 30), (6, 30), (3, 31)]
    while len(periods) < count:
        for month, day in quarter_ends:
            candidate = date(year, month, day)
            if candidate <= today:
                periods.append(candidate.strftime("%Y%m%d"))
                if len(periods) >= count:
                    break
        year -= 1
    return periods


def _market_code(ts_code: str) -> str:
    suffix = ts_code.rsplit(".", 1)[-1].lower() if "." in ts_code else ""
    return {"sh": "sh", "sz": "sz", "bj": "bj"}.get(suffix, suffix)

const STOCKS = [
  ["600519.SH", "贵州茅台", "sh", "食品饮料", "白酒", "消费"],
  ["600036.SH", "招商银行", "sh", "银行", "股份制银行", "金融"],
  ["000333.SZ", "美的集团", "sz", "家用电器", "白色家电", "消费"],
  ["300750.SZ", "宁德时代", "sz", "电力设备", "动力电池", "新能源车"],
  ["002594.SZ", "比亚迪", "sz", "汽车", "新能源整车", "新能源车"],
  ["300308.SZ", "中际旭创", "sz", "通信", "光模块", "AI算力"],
  ["300502.SZ", "新易盛", "sz", "通信", "光模块", "AI算力"],
  ["688041.SH", "海光信息", "sh", "半导体", "CPU/GPU", "国产算力"],
  ["603986.SH", "兆易创新", "sh", "半导体", "存储芯片", "国产替代"],
  ["002463.SZ", "沪电股份", "sz", "电子", "PCB", "AI算力"],
  ["688017.SH", "绿的谐波", "sh", "机械设备", "减速器", "机器人"],
  ["600900.SH", "长江电力", "sh", "公用事业", "水电", "高股息"],
  ["601088.SH", "中国神华", "sh", "煤炭", "动力煤", "高股息"],
  ["920106.BJ", "林泰新材", "bj", "汽车零部件", "传动系统", "北交所"],
];

const PLAN = {
  good_company: [0, 1, 2, 3, 11, 12],
  good_timing: [1, 2, 5, 6, 8, 9, 13],
  good_price: [0, 1, 2, 8, 11, 12],
  good_story: [3, 4, 5, 7, 8, 10],
  good_stock: [3, 5, 6, 7, 9, 10, 13],
};

export const MOCK_FIVE_GOOD_ENTRIES = Object.entries(PLAN).flatMap(([category, indexes]) =>
  indexes.map((index, order) => makeEntry(category, STOCKS[index], index, order)),
);

export const MOCK_FIVE_GOOD_HISTORY = MOCK_FIVE_GOOD_ENTRIES.map((entry) => ({
  id: `history-${entry.id}`,
  entryId: entry.id,
  stockCode: entry.stockCode,
  category: entry.category,
  action: "added",
  newScore: entry.score,
  reason: entry.selectionReason,
  asOfDate: entry.asOfDate,
  createdAt: entry.validFrom,
}));

function makeEntry(category, stock, index, order) {
  const [stockCode, stockName, market, industry, subIndustry, sector] = stock;
  const score = Math.min(96, 92 - order * 2 + index % 3);
  const updatedAt = new Date(Date.now() - ((order * 7 + index) % 90) * 3600_000).toISOString();
  return {
    id: `mock-${category}-${stockCode}`,
    stockCode, stockName, market, category, industry, subIndustry, sector, score,
    status: order < 5 ? "active" : "watching",
    selectionReason: reason(category, stockName, subIndustry),
    riskSummary: "行业景气、盈利兑现和市场波动可能导致筛选结论变化。",
    tags: tags(category),
    validFrom: new Date(Date.now() - 20 * 86400_000).toISOString(),
    validUntil: category === "good_timing" ? new Date(Date.now() + 7 * 86400_000).toISOString() : null,
    asOfDate: updatedAt.slice(0, 10), createdAt: updatedAt, updatedAt,
    categoryData: categoryData(category, index, score, sector, subIndustry),
  };
}

function reason(category, name, subIndustry) {
  return {
    good_company: `${name}在${subIndustry}领域具备较强市场地位，适合长期基本面跟踪。`,
    good_timing: "价格处于震荡区间附近，量价和资金信号进入观察阶段。",
    good_price: "估值较自身历史区间回落，仍需盈利兑现验证。",
    good_story: "产业或公司事件可能改变市场关注度，后续需交叉验证。",
    good_stock: `位于${subIndustry}核心环节，对产业景气变化具有较高弹性。`,
  }[category];
}

function tags(category) {
  return {
    good_company: ["行业龙头", "盈利稳定"], good_timing: ["区间观察", "量价跟踪"],
    good_price: ["估值回落", "历史分位"], good_story: ["事件催化", "持续跟踪"],
    good_stock: ["产业弹性", "热点关联"],
  }[category];
}

function categoryData(category, index, score, sector, subIndustry) {
  const price = Number((18 + index * 13.7).toFixed(2));
  if (category === "good_company") return {industryPosition: "行业第一梯队", roe: 13 + index, grossMargin: 25 + index * 2, cashflowQuality: "稳健", dividendYield: 1.5 + index * .3, profitGrowth: "稳定增长", competitiveAdvantage: "品牌、规模与客户壁垒"};
  if (category === "good_timing") return {currentPrice: price, boxLow: price * .91, boxHigh: price * 1.12, watchZoneLow: price * .95, watchZoneHigh: price * 1.02, supportPrice: price * .9, invalidPrice: price * .87, technicalSignal: ["回踩", "震荡", "突破观察"][index % 3], capitalFlowSignal: ["流入", "平稳", "数据不足"][index % 3]};
  if (category === "good_price") return {currentPrice: price, pe: 10 + index * 2.7, pb: 1.1 + index * .28, peg: .72 + index * .09, historicalPePercentile: 18 + index * 4, profitGrowthExpectation: `${12 + index % 7 * 3}%`, valuationStatus: ["偏低", "合理", "数据不足"][index % 3], industryValuationComparison: "接近行业中位数"};
  if (category === "good_story") return {eventId: "", eventTitle: "产业催化与公司进展跟踪", eventType: "产业催化", eventScore: score, eventStatus: "tracking", impactDirection: "mixed", confidence: 68, catalystSummary: "关注公告、订单和财务兑现。"};
  return {industryChain: sector, chainPosition: subIndustry, elasticityScore: score, themeRelevanceScore: Math.min(96, score + 3), relativeStrength: "强", marketCapLevel: ["大盘", "中盘", "小盘"][index % 3], catalystSummary: "产业景气、订单与板块热度共同驱动。"};
}

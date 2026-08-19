/**
 * @typedef {"policy"|"industry"|"earnings"|"company"|"overseas"|"risk"} EventCategory
 * @typedef {"unverified"|"confirmed"|"tracking"|"completed"|"corrected"} EventStatus
 * @typedef {"positive"|"negative"|"mixed"|"neutral"|"uncertain"} ImpactDirection
 * @typedef {"intraday"|"short_term"|"medium_term"|"long_term"} ImpactHorizon
 *
 * @typedef {Object} EventTimelineItem
 * @property {string} id
 * @property {string} occurredAt
 * @property {string} title
 * @property {string} summary
 * @property {EventStatus=} status
 *
 * @typedef {Object} MarketEvent
 * @property {string} id
 * @property {string} title
 * @property {EventCategory} category
 * @property {string} occurredAt
 * @property {string} firstSeenAt
 * @property {string} updatedAt
 * @property {string} factSummary
 * @property {string} importanceReason
 * @property {ImpactDirection} impactDirection
 * @property {ImpactHorizon} impactHorizon
 * @property {EventStatus} status
 * @property {string[]} sectors
 * @property {string[]} concepts
 * @property {string[]} companies
 * @property {number} sourceCount
 * @property {number} updateCount
 * @property {number} importanceScore
 * @property {number} confidenceScore
 * @property {string=} sourceUrl
 * @property {EventTimelineItem[]=} timeline
 *
 * @typedef {Object} SectorHeatItem
 * @property {string} id
 * @property {string} name
 * @property {number} heatScore
 * @property {number} heatChange
 * @property {number} eventCount
 * @property {string} primaryCatalyst
 *
 * @typedef {Object} EventDistributionItem
 * @property {EventCategory} category
 * @property {number} count
 * @property {number} percentage
 *
 * @typedef {Object} EventDashboardData
 * @property {MarketEvent[]} topEvents
 * @property {MarketEvent[]} eventFeed
 * @property {SectorHeatItem[]} hotSectors
 * @property {EventDistributionItem[]} eventDistribution
 * @property {MarketEvent[]} trackingEvents
 */

export const EVENT_CATEGORY_LABELS = {
  policy: "政策",
  industry: "产业",
  earnings: "财报",
  company: "公司",
  overseas: "海外",
  risk: "风险",
};

export const EVENT_STATUS_LABELS = {
  unverified: "待验证",
  confirmed: "已确认",
  tracking: "持续跟踪",
  completed: "已完成",
  corrected: "已修正",
};

export const IMPACT_LABELS = {
  positive: "利好",
  negative: "利空",
  mixed: "分歧",
  neutral: "中性",
  uncertain: "观察",
};

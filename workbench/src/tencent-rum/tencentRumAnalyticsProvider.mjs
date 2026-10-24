import { createTencentRumClient } from './tencentRumClient.mjs';
import { aggregatePageRecords, attachEngagement, dimensionsFromV2, platformDimensions } from './record-aggregates.mjs';

const DAY_MS = 24 * 60 * 60 * 1000;
const MINUTE_MS = 60 * 1000;
const DEFAULT_CACHE_MS = 60 * 1000;
const MAX_CACHE_MS = 10 * MINUTE_MS;
const RUM_RAW_LOG_RETENTION_DAYS = 30;
const RUM_LOG_PAGE_SIZE = 100;
const MAX_RUM_LOG_PAGES = 100;
const RUM_BUSINESS_EVENT_NAMES = new Set([
  'engagement',
  'session_end',
  'cta_impression',
  'cta_click',
  'wechat_entry_impression',
  'wechat_entry_click',
  'wechat_qr_view',
  'wechat_id_copy',
  'submit_ai_training_consultation',
  'development_email_click',
]);
const RUM_WECHAT_FUNNEL_EVENTS = [
  'wechat_entry_impression',
  'wechat_entry_click',
  'wechat_qr_view',
  'wechat_id_copy',
];
const RUM_ENVIRONMENTS = new Set([
  'production',
  'gray',
  'pre',
  'daily',
  'local',
  'test',
  'others',
]);
const REQUIRED_CLIENT_METHODS = [
  'describeScoresV2',
  'describePvList',
  'describeUvList',
  'describeProjectLimits',
  'describeDataPvUrlStatisticsV2',
];
const SHANGHAI_PARTS = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  hourCycle: 'h23',
});

export const TENCENT_RUM_ENVIRONMENT_KEYS = Object.freeze([
  'TENCENTCLOUD_RUM_SECRET_ID',
  'TENCENTCLOUD_RUM_SECRET_KEY',
  'TENCENTCLOUD_RUM_SESSION_TOKEN',
  'TENCENTCLOUD_RUM_REGION',
  'TENCENTCLOUD_RUM_PROJECT_ID',
  'TENCENTCLOUD_RUM_ENV',
  'TENCENTCLOUD_RUM_CACHE_MS',
  'TENCENTCLOUD_RUM_BUSINESS_CUTOVER_AT',
]);

export class TencentRumAnalyticsProviderError extends Error {
  constructor(code, field) {
    super(code);
    this.name = 'TencentRumAnalyticsProviderError';
    this.code = code;
    if (field) this.field = field;
  }

  toJSON() {
    return Object.fromEntries(
      [
        ['name', this.name],
        ['code', this.code],
        ['field', this.field],
      ].filter(([, value]) => value !== undefined)
    );
  }
}

function providerError(code, field) {
  throw new TencentRumAnalyticsProviderError(code, field);
}

function normalizeInteger(value, field, min, max) {
  const number = typeof value === 'number' ? value : Number(value);
  if (!Number.isInteger(number) || number < min || number > max) {
    providerError(`invalid_${field}`, field);
  }
  return number;
}

function normalizeDate(value) {
  const date = value instanceof Date ? new Date(value.getTime()) : new Date(value);
  if (Number.isNaN(date.getTime())) providerError('invalid_analytics_now', 'now');
  return date;
}

function shanghaiParts(value) {
  const output = {};
  for (const part of SHANGHAI_PARTS.formatToParts(value)) {
    if (part.type !== 'literal') output[part.type] = part.value;
  }
  return output;
}

function formatTencentHour(value) {
  const parts = shanghaiParts(value);
  return `${parts.year}${parts.month}${parts.day}${parts.hour}`;
}

function formatTencentDay(value) {
  const parts = shanghaiParts(value);
  return `${parts.year}${parts.month}${parts.day}`;
}

function isoRange(startMs, endMs, extra = {}) {
  return {
    ...extra,
    start: new Date(startMs).toISOString(),
    end: new Date(endMs).toISOString(),
    timeZone: 'Asia/Shanghai',
  };
}

function comparisonRange(days, endMs) {
  const startMs = endMs - days * DAY_MS;
  return {
    current: { startMs, endMs },
    previous: { startMs: startMs - days * DAY_MS, endMs: startMs },
  };
}

function scoreRequestRange(range) {
  return {
    startTime: formatTencentHour(new Date(range.startMs)),
    endTime: formatTencentHour(new Date(range.endMs)),
  };
}

function listRequestRange(range) {
  // PV/UV list endpoints are day based and document an inclusive end date.
  return {
    startTime: formatTencentDay(new Date(range.startMs)),
    endTime: formatTencentDay(new Date(Math.max(range.startMs, range.endMs - 1))),
  };
}

function nullableNumber(value){return value==null||value===''?null:finiteNumber(value,null);}

function finiteNumber(value, fallback = 0) {
  const number = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(number) && number >= 0 ? number : fallback;
}

function scoreForProject(response, projectId) {
  const scores = Array.isArray(response?.ScoreSet) ? response.ScoreSet : [];
  return scores.find((score) => Number(score?.ProjectID) === projectId) || {};
}

function conversions() {
  return { total: 0, aiTrainingConsultations: 0, developmentEmailClicks: 0 };
}

function sortedJourneyRows(values) {
  return [...values].sort(
    (left, right) =>
      right.sessions - left.sessions ||
      right.count - left.count ||
      String(left.path || left.toPath || '').localeCompare(String(right.path || right.toPath || ''))
  );
}

function pageJourneysFromRumLogs(records, coverage = {}) {
  const sessions = new Map();
  for (const record of records) {
    const list = sessions.get(record.sessionHash) || [];
    list.push(record);
    sessions.set(record.sessionHash, list);
  }

  const transitions = new Map();
  const homeNextPages = new Map();
  const entryPages = new Map();
  const sequences = new Map();
  for (const rows of sessions.values()) {
    rows.sort((left, right) => left.timestamp.localeCompare(right.timestamp));
    const compact = [];
    for (const row of rows) {
      if (compact.at(-1)?.path !== row.path) compact.push(row);
    }
    if (!compact.length) continue;
    const entry = compact[0];
    const entryValue = entryPages.get(entry.path) || {
      path: entry.path,
      count: 0,
      sessions: 0,
      visitors: new Set(),
    };
    entryValue.count += 1;
    entryValue.sessions += 1;
    entryValue.visitors.add(entry.visitorHash);
    entryPages.set(entry.path, entryValue);

    if (entry.path === '/') {
      const next = compact.slice(1).find((row) => row.path !== '/');
      if (next) {
        const value = homeNextPages.get(next.path) || {
          path: next.path,
          count: 0,
          sessions: 0,
          visitors: new Set(),
        };
        value.count += 1;
        value.sessions += 1;
        value.visitors.add(next.visitorHash);
        homeNextPages.set(next.path, value);
      }
    }

    for (let index = 1; index < compact.length; index += 1) {
      const previous = compact[index - 1];
      const current = compact[index];
      const key = `${previous.path}\0${current.path}`;
      const value = transitions.get(key) || {
        fromPath: previous.path,
        toPath: current.path,
        count: 0,
        sessions: new Set(),
        visitors: new Set(),
      };
      value.count += 1;
      value.sessions.add(current.sessionHash);
      value.visitors.add(current.visitorHash);
      transitions.set(key, value);
    }

    if (compact.length > 1) {
      const paths = compact.slice(0, 4).map((row) => row.path);
      const key = paths.join('\0');
      const value = sequences.get(key) || { paths, count: 0, sessions: 0 };
      value.count += 1;
      value.sessions += 1;
      sequences.set(key, value);
    }
  }

  const publicCount = (value) => ({
    path: value.path,
    count: value.count,
    sessions: value.sessions,
    visitors: value.visitors.size,
  });
  return Object.freeze({
    transitions: Object.freeze(
      [...transitions.values()]
        .map((value) => ({
          fromPath: value.fromPath,
          toPath: value.toPath,
          count: value.count,
          sessions: value.sessions.size,
          visitors: value.visitors.size,
        }))
        .sort((left, right) => right.sessions - left.sessions || right.count - left.count)
    ),
    homeNextPages: Object.freeze(sortedJourneyRows([...homeNextPages.values()].map(publicCount))),
    entryPages: Object.freeze(sortedJourneyRows([...entryPages.values()].map(publicCount))),
    topSequences: Object.freeze(
      [...sequences.values()].sort(
        (left, right) => right.sessions - left.sessions || right.count - left.count
      )
    ),
    coverage: Object.freeze({
      source: 'tencent-rum-page-logs',
      ...coverage,
    }),
  });
}

function ratio(numerator, denominator) {
  return denominator > 0 ? Math.round((numerator / denominator) * 10_000) / 10_000 : null;
}

function businessFromRumLogs(records, coverage = {}) {
  const seen = new Set();
  let duplicateLogs = 0;
  const valid = [];
  for (const record of records) {
    if (!RUM_BUSINESS_EVENT_NAMES.has(record.eventName)) continue;
    const fingerprint = [
      record.timestamp,
      record.sessionHash,
      record.visitorHash,
      record.eventName,
      record.ctaId,
      record.placement,
      record.path,
      record.activeMs,
      record.scrollBucket,
    ].join('\0');
    if (seen.has(fingerprint)) {
      duplicateLogs += 1;
      continue;
    }
    seen.add(fingerprint);
    valid.push(record);
  }
  const ctaRows = new Map();
  let impressions = 0;
  let clicks = 0;
  for (const record of valid) {
    if (record.eventName !== 'cta_impression' && record.eventName !== 'cta_click') continue;
    if (!record.ctaId || !record.placement) continue;
    const key = `${record.ctaId}\0${record.placement}`;
    const value = ctaRows.get(key) || {
      ctaId: record.ctaId,
      placement: record.placement,
      impressions: 0,
      clicks: 0,
      sessionHashes: new Set(),
    };
    value.sessionHashes.add(record.sessionHash);
    if (record.eventName === 'cta_impression') {
      impressions += 1;
      value.impressions += 1;
    } else {
      clicks += 1;
      value.clicks += 1;
    }
    ctaRows.set(key, value);
  }
  const cta = {
    impressions,
    clicks,
    clickThroughRate: ratio(clicks, impressions),
    byCta: [...ctaRows.values()]
      .map(({sessionHashes,...row}) => ({ ...row, sessions:sessionHashes.size, clickThroughRate: ratio(row.clicks, row.impressions) }))
      .sort((left, right) => right.impressions - left.impressions || right.clicks - left.clicks),
  };

  const wechatSteps = RUM_WECHAT_FUNNEL_EVENTS.map((eventName) => {
    const rows = valid.filter((record) => record.eventName === eventName);
    return {
      eventName,
      count: rows.length,
      sessions: new Set(rows.map((row) => row.sessionHash)).size,
      visitors: new Set(rows.map((row) => row.visitorHash)).size,
    };
  });
  const sessionCounts = new Map(wechatSteps.map((step) => [step.eventName, step.sessions]));
  const entryImpressions = sessionCounts.get('wechat_entry_impression') || 0;
  const entryClicks = sessionCounts.get('wechat_entry_click') || 0;
  const qrViews = sessionCounts.get('wechat_qr_view') || 0;
  const idCopies = sessionCounts.get('wechat_id_copy') || 0;
  const wechatByPlacement = new Map();
  for (const record of valid) {
    if (!RUM_WECHAT_FUNNEL_EVENTS.includes(record.eventName) || !record.placement) continue;
    const value = wechatByPlacement.get(record.placement) || {
      placement: record.placement,
      events: 0,
      sessions: new Set(),
      visitors: new Set(),
    };
    value.events += 1;
    value.sessions.add(record.sessionHash);
    value.visitors.add(record.visitorHash);
    wechatByPlacement.set(record.placement, value);
  }
  const intentSessions = new Set(
    valid
      .filter((record) => record.eventName === 'wechat_entry_click' || record.eventName === 'wechat_qr_view')
      .map((record) => record.sessionHash)
  ).size;
  const engagementRows = valid.filter(
    (record) => record.eventName === 'engagement' || record.eventName === 'session_end'
  );
  const sessionDurations = new Map();
  const pathDurations = new Map();
  for (const record of engagementRows) {
    const activeMs = finiteNumber(record.activeMs);
    sessionDurations.set(
      record.sessionHash,
      (sessionDurations.get(record.sessionHash) || 0) + activeMs
    );
    const value = pathDurations.get(record.path) || {
      path: record.path,
      activeMs: 0,
      events: 0,
      sessions: new Set(),
      maxScrollBucket: 0,
    };
    value.activeMs += activeMs;
    value.events += 1;
    value.sessions.add(record.sessionHash);
    value.maxScrollBucket = Math.max(value.maxScrollBucket, finiteNumber(record.scrollBucket));
    pathDurations.set(record.path, value);
  }
  const totalActiveMs = [...sessionDurations.values()].reduce((sum, value) => sum + value, 0);
  const engagement = Object.freeze({
    events: engagementRows.length,
    sessions: sessionDurations.size,
    totalActiveMs,
    avgDurationSeconds: sessionDurations.size
      ? Math.round((totalActiveMs / sessionDurations.size / 1000) * 10) / 10
      : 0,
    byPath: Object.freeze(
      [...pathDurations.values()]
        .map((value) => ({
          path: value.path,
          events: value.events,
          sessions: value.sessions.size,
          activeMs: value.activeMs,
          avgDurationSeconds: value.sessions.size
            ? Math.round((value.activeMs / value.sessions.size / 1000) * 10) / 10
            : 0,
          maxScrollBucket: value.maxScrollBucket,
        }))
        .sort((left, right) => right.activeMs - left.activeMs || left.path.localeCompare(right.path))
    ),
    sessionDefinition: 'aegis-sdk-session-approximation',
  });
  const aiTrainingSessions = new Set(
    valid
      .filter((record) => record.eventName === 'submit_ai_training_consultation')
      .map((record) => record.sessionHash)
  );
  const developmentEmailSessions = new Set(
    valid
      .filter((record) => record.eventName === 'development_email_click')
      .map((record) => record.sessionHash)
  );
  const convertedSessions = new Set([...aiTrainingSessions, ...developmentEmailSessions]);
  return Object.freeze({
    cta: Object.freeze(cta),
    wechatFunnel: Object.freeze({
      steps: Object.freeze(wechatSteps),
      intentSessions,
      byPlacement: Object.freeze(
        [...wechatByPlacement.values()]
          .map((value) => ({
            placement: value.placement,
            events: value.events,
            sessions: value.sessions.size,
            visitors: value.visitors.size,
          }))
          .sort((left, right) => right.sessions - left.sessions || left.placement.localeCompare(right.placement))
      ),
      rates: Object.freeze({
        entryClickRate: ratio(entryClicks, entryImpressions),
        qrViewRate: ratio(qrViews, entryClicks),
        idCopyRate: ratio(idCopies, qrViews),
        overallCopyRate: ratio(idCopies, entryImpressions),
      }),
    }),
    engagement,
    actions: ['submit_ai_training_consultation','development_email_click'].map(eventName=>{const rows=valid.filter(r=>r.eventName===eventName);return {eventName,events:rows.length,sessions:new Set(rows.map(r=>r.sessionHash)).size};}).filter(row=>row.events>0),
    conversions: Object.freeze({
      total: convertedSessions.size,
      aiTrainingConsultations: aiTrainingSessions.size,
      developmentEmailClicks: developmentEmailSessions.size,
    }),
    coverage: Object.freeze({
      source: 'tencent-rum-event-logs',
      ...coverage,
      acceptedEvents: valid.length,
      duplicateLogs,
    }),
  });
}

function metricsFromScore(score) {
  return {
    pv: finiteNumber(score.PagePv),
    uv: finiteNumber(score.PageUv),
    sessions: null,
    newVisitors: 0,
    avgDurationSeconds: 0,
    filteredRequests: 0,
    conversions: conversions(),
    pageErrors: nullableNumber(score.PageError),
    score: nullableNumber(score.Score),
    pagePerformanceScore: nullableNumber(score.PagePerformanceScore),
    jsErrorScore: nullableNumber(score.JsErrorScore),
  };
}

function performanceFromScore(score) {
  return {
    firstScreenMs: nullableNumber(score.PageDuration),
    lcpMs: nullableNumber(score.PageLCP),
    fidMs: nullableNumber(score.PageFID),
    cls: nullableNumber(score.PageCLS),
    fcpMs: nullableNumber(score.PageFCP),
    inpMs: nullableNumber(score.PageINP),
    apiAverageMs: nullableNumber(score.ApiDuration),
    staticAverageMs: nullableNumber(score.StaticDuration),
  };
}

function dateFromMetricTime(value) {
  const text = String(value || '');
  const match = text.match(/^(\d{4})(\d{2})(\d{2})/);
  if (!match) return '';
  const date = `${match[1]}-${match[2]}-${match[3]}`;
  return Number.isNaN(new Date(`${date}T00:00:00.000Z`).getTime()) ? '' : date;
}

function metricMap(records, valueKey, projectId) {
  const map = new Map();
  for (const record of Array.isArray(records) ? records : []) {
    if (Number(record?.ProjectId) !== projectId) continue;
    const date = dateFromMetricTime(record.CreateTime);
    if (!date) continue;
    map.set(date, finiteNumber(record[valueKey]));
  }
  return map;
}

function trendFromLists(pvResponse, uvResponse, projectId) {
  const pv = metricMap(pvResponse?.ProjectPvSet, 'Pv', projectId);
  const uv = metricMap(uvResponse?.ProjectUvSet, 'Uv', projectId);
  return [...new Set([...pv.keys(), ...uv.keys()])]
    .sort()
    .map((date) => ({
      date,
      pv: pv.get(date) || 0,
      uv: uv.get(date) || 0,
      sessions: 0,
      filteredRequests: 0,
      newVisitors: 0,
      avgDurationSeconds: 0,
      conversions: 0,
    }));
}

function v2Rows(response) {
  const results = Array.isArray(response?.Result?.results) ? response.Result.results : [];
  const rows = [];
  for (const result of results) {
    for (const series of Array.isArray(result?.series) ? result.series : []) {
      if (Array.isArray(series?.rows)) rows.push(...series.rows.map(row => ({ ...series.tags, ...row })));
    }
  }
  return rows;
}

function normalizedKey(value) {
  return String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
}

function rowValue(row, candidates) {
  if (!row || typeof row !== 'object') return undefined;
  const wanted = new Set(candidates.map(normalizedKey));
  const entry = Object.entries(row).find(([key]) => wanted.has(normalizedKey(key)));
  return entry?.[1];
}

function rowCount(row) {
  const direct = rowValue(row, ['pv', 'count', 'allCount', 'CK_PV_COUNT', 'pagepv', 'page_pv', 'value', 'views', 'total']);
  if (direct !== undefined) return finiteNumber(direct);
  const numeric = Object.values(row).filter(
    (value) => typeof value === 'number' || (typeof value === 'string' && /^\d+(?:\.\d+)?$/.test(value))
  );
  return numeric.length === 1 ? finiteNumber(numeric[0]) : 0;
}

function sanitizePagePath(value) {
  if (typeof value !== 'string' || value.length < 1 || value.length > 2048) return '';
  try {
    const url = new URL(value, 'https://example.invalid');
    if (!['http:', 'https:'].includes(url.protocol)) return '';
    const path = url.pathname;
    return sanitizePathSegments(path);
  } catch {
    const path = value.split(/[?#]/, 1)[0];
    return sanitizePathSegments(path);
  }
}

function sanitizePathSegments(path) {
  if (
    typeof path !== 'string' ||
    !path.startsWith('/') ||
    path.startsWith('//') ||
    path.length > 300 ||
    /[\u0000-\u001f\u007f]/.test(path)
  ) {
    return '';
  }
  return path
    .split('/')
    .map((segment) => {
      if (!segment) return segment;
      let decoded = segment;
      try {
        decoded = decodeURIComponent(segment);
      } catch {
        // Keep malformed encoding opaque; the checks below still apply to its literal form.
      }
      const publicEventId = /^evt_[a-f0-9]{20}$/i.test(decoded);
      const readableSlug = decoded
        .split('-')
        .every((part) => /^[A-Za-z]+$/.test(part) || /^\d{1,4}$/.test(part));
      const sensitive =
        /@/.test(decoded) ||
        /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(decoded) ||
        (!publicEventId && /^\d{7,}$/.test(decoded)) ||
        (!publicEventId && !readableSlug && /^[A-Za-z0-9_-]{24,}$/.test(decoded) && /[A-Za-z]/.test(decoded) && /\d/.test(decoded));
      return sensitive ? ':redacted' : segment;
    })
    .join('/');
}

function topPagesFromV2(response) {
  const grouped = new Map();
  for (const row of v2Rows(response)) {
    const path = sanitizePagePath(rowValue(row, ['url', 'path', 'from', 'name', 'key']));
    if (!path) continue;
    grouped.set(path, (grouped.get(path) || 0) + rowCount(row));
  }
  return [...grouped.entries()]
    .map(([path, pv]) => ({ path, pv, uv: 0, sessions: 0, avgDurationSeconds: 0 }))
    .sort((left, right) => right.pv - left.pv || left.path.localeCompare(right.path))
    .slice(0, 20);
}

function sourceCategory(value) {
  if (value === null || value === undefined) return 'direct';
  const text = String(value).trim();
  if (!text || text === '-') return 'direct';
  if (['direct', 'internal', 'organic', 'social', 'referral'].includes(text.toLowerCase())) {
    return text.toLowerCase();
  }
  let hostname = '';
  try {
    hostname = new URL(text, 'https://example.invalid').hostname.toLowerCase();
  } catch {
    return 'referral';
  }
  if (hostname === 'example.invalid' || hostname.endsWith('.example.invalid')) return 'internal';
  if (/(^|\.)(baidu|google|bing|sogou|so|duckduckgo)\./.test(hostname)) return 'organic';
  if (
    /(^|\.)(weixin|wechat|weibo|zhihu|xiaohongshu|douyin|tiktok|bilibili|x|twitter|feishu)\./.test(hostname) ||
    hostname === 't.co'
  ) {
    return 'social';
  }
  return 'referral';
}

function sourcesFromV2(response) {
  const grouped = new Map();
  for (const row of v2Rows(response)) {
    const category = sourceCategory(rowValue(row, ['from', 'source', 'referer', 'referrer', 'url', 'name', 'key']));
    grouped.set(category, (grouped.get(category) || 0) + rowCount(row));
  }
  return [...grouped.entries()]
    .map(([name, pv]) => ({ name, pv, uv: null }))
    .sort((left, right) => right.pv - left.pv || left.name.localeCompare(right.name));
}

function deviceCategory(value) {
  const text = String(value || '').toLowerCase();
  if (/ipad|tablet|pad/.test(text)) return 'tablet';
  if (/mobile|android|iphone|ipod|phone/.test(text)) return 'mobile';
  if (/desktop|windows|macintosh|macos|macbook|linux|pc/.test(text)) return 'desktop';
  return 'unknown';
}

function devicesFromV2(response) {
  const grouped = new Map();
  for (const row of v2Rows(response)) {
    const category = deviceCategory(rowValue(row, ['device', 'platform', 'name', 'key']));
    grouped.set(category, (grouped.get(category) || 0) + rowCount(row));
  }
  return [...grouped.entries()]
    .map(([name, pv]) => ({ name, pv, uv: null }))
    .sort((left, right) => right.pv - left.pv || left.name.localeCompare(right.name));
}

function aggregateCountFromV2(response, candidates) {
  const values = [];
  for (const row of v2Rows(response)) {
    const direct = rowValue(row, candidates);
    if (direct !== undefined) values.push(finiteNumber(direct));
    else if (Object.keys(row).length === 1) values.push(finiteNumber(Object.values(row)[0]));
  }
  // `ckpv` and `ckuv` are whole-window aggregates. Taking max avoids summing a time series of
  // already de-duplicated UV values if Tencent returns more than one statement.
  return values.length ? Math.max(...values) : 0;
}

function samplingInfo(response) {
  const entries = Array.isArray(response?.ProjectLimitSet) ? response.ProjectLimitSet : [];
  const interfaces = entries
    .filter((entry) => Number(entry?.ReportType) === 1)
    .map((entry) => ({
      interface: String(entry.ProjectInterface || 'unknown'),
      rate: Math.max(0, Math.min(1, finiteNumber(entry.ReportRate) / 100)),
    }))
    .sort((left, right) => left.interface.localeCompare(right.interface));
  return {
    samplingRate: interfaces.length ? Math.min(...interfaces.map((entry) => entry.rate)) : 1,
    samplingInterfaces: interfaces,
  };
}

function assertClient(client) {
  if (!client || typeof client !== 'object') providerError('invalid_rum_client', 'client');
  for (const method of REQUIRED_CLIENT_METHODS) {
    if (typeof client[method] !== 'function') providerError('invalid_rum_client', 'client');
  }
  return client;
}

function requiredEnvironmentValue(environment, key) {
  const value = environment?.[key];
  if (typeof value !== 'string' || value.length < 1 || value.trim() !== value) {
    providerError('invalid_rum_environment', key);
  }
  return value;
}

function normalizeRumEnvironment(value, errorField = 'rum_environment') {
  if (typeof value !== 'string' || !RUM_ENVIRONMENTS.has(value)) {
    providerError('invalid_rum_environment', errorField);
  }
  return value;
}

export function readTencentRumEnvironmentConfig(environment = {}) {
  if (!environment || typeof environment !== 'object' || Array.isArray(environment)) {
    providerError('invalid_rum_environment', 'environment');
  }
  const projectIdText = requiredEnvironmentValue(environment, 'TENCENTCLOUD_RUM_PROJECT_ID');
  if (!/^\d{1,10}$/.test(projectIdText)) providerError('invalid_rum_environment', 'TENCENTCLOUD_RUM_PROJECT_ID');
  const cacheText = environment.TENCENTCLOUD_RUM_CACHE_MS;
  if (cacheText !== undefined && (typeof cacheText !== 'string' || !/^\d{1,7}$/.test(cacheText))) {
    providerError('invalid_rum_environment', 'TENCENTCLOUD_RUM_CACHE_MS');
  }
  const sessionToken = environment.TENCENTCLOUD_RUM_SESSION_TOKEN;
  if (sessionToken !== undefined && typeof sessionToken !== 'string') {
    providerError('invalid_rum_environment', 'TENCENTCLOUD_RUM_SESSION_TOKEN');
  }
  const businessCutoverAt = environment.TENCENTCLOUD_RUM_BUSINESS_CUTOVER_AT;
  if (
    businessCutoverAt !== undefined &&
    (
      typeof businessCutoverAt !== 'string' ||
      !businessCutoverAt ||
      !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(businessCutoverAt) ||
      Number.isNaN(Date.parse(businessCutoverAt)) ||
      new Date(businessCutoverAt).toISOString() !== businessCutoverAt
    )
  ) {
    providerError('invalid_rum_environment', 'TENCENTCLOUD_RUM_BUSINESS_CUTOVER_AT');
  }
  return Object.freeze({
    secretId: requiredEnvironmentValue(environment, 'TENCENTCLOUD_RUM_SECRET_ID'),
    secretKey: requiredEnvironmentValue(environment, 'TENCENTCLOUD_RUM_SECRET_KEY'),
    sessionToken: sessionToken || '',
    region: requiredEnvironmentValue(environment, 'TENCENTCLOUD_RUM_REGION'),
    projectId: normalizeInteger(projectIdText, 'rum_project_id', 1, 2_147_483_647),
    environment: normalizeRumEnvironment(
      requiredEnvironmentValue(environment, 'TENCENTCLOUD_RUM_ENV'),
      'TENCENTCLOUD_RUM_ENV'
    ),
    cacheMs: normalizeInteger(cacheText ?? DEFAULT_CACHE_MS, 'rum_cache_ms', 0, MAX_CACHE_MS),
    businessCutoverAt: businessCutoverAt || '',
  });
}

export function createTencentRumAnalyticsProvider({
  client,
  projectId,
  environment = 'production',
  cacheMs = DEFAULT_CACHE_MS,
  businessCutoverAt = '',
  queryMinIntervalMs = 0,
} = {}) {
  const originalClient = assertClient(client);
  let activeQueries=0,lastQueryStart=0;const queue=[];
  async function limitedQuery(task){
    if(activeQueries>=6)await new Promise(resolve=>queue.push(resolve));else activeQueries++;
    const start=Math.max(Date.now(),lastQueryStart+queryMinIntervalMs);lastQueryStart=start;
    try{if(start>Date.now())await new Promise(resolve=>setTimeout(resolve,start-Date.now()));return await task();}
    finally{const next=queue.shift();if(next)next();else activeQueries--;}
  }
  const normalizedClient = Object.fromEntries([...REQUIRED_CLIENT_METHODS,'describeRumLogList'].map(name=>{
    const fn=originalClient[name];if(typeof fn!=='function')return [name,undefined];
    return [name,async (...args)=>{for(let attempt=0;;attempt++){try{return await limitedQuery(()=>fn.apply(originalClient,args));}catch(error){
      const retryable=['transport_error','http_error'].includes(error.code)||/^RequestLimitExceeded|^InternalError|^FailedOperation.InfrastructureError/.test(error.providerCode||'');
      if(!retryable||attempt>=2)throw error;await new Promise(resolve=>setTimeout(resolve,500*2**attempt));
    }}}];
  }));
  const normalizedProjectId = normalizeInteger(projectId, 'rum_project_id', 1, 2_147_483_647);
  const normalizedEnvironment = normalizeRumEnvironment(environment);
  const normalizedCacheMs = normalizeInteger(cacheMs, 'rum_cache_ms', 0, MAX_CACHE_MS);
  const normalizedBusinessCutoverMs = businessCutoverAt
    ? Date.parse(businessCutoverAt)
    : null;
  if (businessCutoverAt && !Number.isFinite(normalizedBusinessCutoverMs)) {
    providerError('invalid_rum_business_cutover_at', 'businessCutoverAt');
  }
  const cache = new Map();

  async function cached(key, nowMs, factory) {
    const current = cache.get(key);
    if (
      current &&
      nowMs >= current.createdAt &&
      nowMs - current.createdAt < normalizedCacheMs
    ) {
      return current.value;
    }
    const value = await factory();
    cache.set(key, { createdAt: nowMs, value });
    return value;
  }

  async function score(range) {
    return normalizedClient.describeScoresV2({
      ...scoreRequestRange(range),
      projectIds: [normalizedProjectId],
      type: 'day',
      env: normalizedEnvironment,
    });
  }

  async function pv(range) {
    return normalizedClient.describePvList({
      projectId: normalizedProjectId,
      ...listRequestRange(range),
      dimension: 'd',
    });
  }

  async function uv(range) {
    return normalizedClient.describeUvList({
      projectId: normalizedProjectId,
      ...listRequestRange(range),
      dimension: 'd',
    });
  }

  async function pageStatistics(range, type) {
    return normalizedClient.describeDataPvUrlStatisticsV2({
      projectId: normalizedProjectId,
      startTime: Math.floor(range.startMs / 1000),
      endTime: Math.max(Math.floor(range.startMs / 1000) + 1, Math.floor(range.endMs / 1000)),
      type,
      env: normalizedEnvironment,
      groupByType: 5,
      granularity: '1d',
    });
  }

  async function pageJourneys(range) {
    if (typeof normalizedClient.describeRumLogList !== 'function') return null;
    const startMs = Math.max(range.startMs, range.endMs - RUM_RAW_LOG_RETENTION_DAYS * DAY_MS);
    const query = `id:${normalizedProjectId} AND type:pv AND env:${normalizedEnvironment}`;
    const records = [];
    let invalidRows = 0;
    let total = 0;
    let pagesRead = 0;
    for (let page = 1; page <= MAX_RUM_LOG_PAGES; page += 1) {
      const result = await normalizedClient.describeRumLogList({
        projectId: normalizedProjectId,
        startTime: startMs,
        endTime: range.endMs,
        orderBy: 'asc',
        limit: RUM_LOG_PAGE_SIZE,
        page,
        query,
      });
      pagesRead = page;
      total = result.total;
      invalidRows += result.invalidRows;
      records.push(...result.records);
      if (records.length + invalidRows >= total || result.records.length + result.invalidRows < RUM_LOG_PAGE_SIZE) {
        break;
      }
    }
    return pageJourneysFromRumLogs(records, {
      start: new Date(startMs).toISOString(),
      end: new Date(range.endMs).toISOString(),
      requestedDays: Math.ceil((range.endMs - range.startMs) / DAY_MS),
      retainedDays: RUM_RAW_LOG_RETENTION_DAYS,
      totalLogs: total,
      acceptedLogs: records.length,
      invalidLogs: invalidRows,
      pagesRead,
      truncated: records.length + invalidRows < total,
      rawIdentifiersReturned: false,
    });
  }

  async function businessEvents(range) {
    const emptyBusinessResult = (status, reason) => Object.freeze({
      coverage: Object.freeze({
        source: 'tencent-rum-event-logs',
        status,
        reason,
        requestedStart: new Date(range.startMs).toISOString(),
        requestedEnd: new Date(range.endMs).toISOString(),
        rawIdentifiersReturned: false,
      }),
    });
    if (normalizedBusinessCutoverMs === null) {
      return emptyBusinessResult('disabled', 'cutover_not_configured');
    }
    if (normalizedBusinessCutoverMs >= range.endMs) {
      return emptyBusinessResult('empty', 'before_cutover');
    }
    if (typeof normalizedClient.describeRumLogList !== 'function') {
      return emptyBusinessResult('unavailable', 'log_api_unavailable');
    }
    const retentionStartMs = range.endMs - RUM_RAW_LOG_RETENTION_DAYS * DAY_MS;
    const startMs = Math.max(
      range.startMs,
      retentionStartMs,
      normalizedBusinessCutoverMs
    );
    const truncationReasons = [];
    if (retentionStartMs > range.startMs) truncationReasons.push('retention_30d');
    if (normalizedBusinessCutoverMs > range.startMs) truncationReasons.push('cutover');
    const query = `id:${normalizedProjectId} AND type:event AND env:${normalizedEnvironment}`;
    const records = [];
    let invalidRows = 0;
    let total = 0;
    let pagesRead = 0;
    for (let page = 1; page <= MAX_RUM_LOG_PAGES; page += 1) {
      const result = await normalizedClient.describeRumLogList({
        projectId: normalizedProjectId,
        startTime: startMs,
        endTime: range.endMs,
        orderBy: 'asc',
        limit: RUM_LOG_PAGE_SIZE,
        page,
        query,
      });
      pagesRead = page;
      total = result.total;
      invalidRows += result.invalidRows;
      records.push(...result.records);
      if (records.length + invalidRows >= total || result.records.length + result.invalidRows < RUM_LOG_PAGE_SIZE) {
        break;
      }
    }
    return businessFromRumLogs(records, {
      start: new Date(startMs).toISOString(),
      end: new Date(range.endMs).toISOString(),
      requestedStart: new Date(range.startMs).toISOString(),
      requestedEnd: new Date(range.endMs).toISOString(),
      status: truncationReasons.length ? 'partial' : records.length ? 'observed' : 'empty',
      truncationReasons: Object.freeze(truncationReasons),
      totalLogs: total,
      acceptedLogs: records.length,
      invalidLogs: invalidRows,
      pagesRead,
      truncated: records.length + invalidRows < total,
      rawIdentifiersReturned: false,
    });
  }

  return Object.freeze({
    async getSummary({ days = 30, now = new Date() } = {}) {
      const normalizedDays = normalizeInteger(days, 'analytics_days', 1, 90);
      const referenceNow = normalizeDate(now);
      // All queries use the same closed-hour boundary, matching the score API's hour precision.
      const endMs = Math.floor(referenceNow.getTime() / (60 * MINUTE_MS)) * (60 * MINUTE_MS);
      return cached(`summary:${normalizedDays}`, referenceNow.getTime(), async () => {
        const range = comparisonRange(normalizedDays, endMs);
        async function logWindow(period,type){
          if(typeof normalizedClient.describeRumLogList!=='function')return {records:[],coverage:{status:'unavailable',complete:false}};
          const startMs=Math.max(period.startMs,endMs-RUM_RAW_LOG_RETENTION_DAYS*DAY_MS,type==='event'&&normalizedBusinessCutoverMs!==null?normalizedBusinessCutoverMs:0);
          if(startMs>=period.endMs)return {records:[],coverage:{status:'unavailable',complete:false,reason:'outside_retention'}};
          const records=[];let invalidRows=0,total=0,pagesRead=0,fetchedRows=0,unstable=false;
          async function fetchPage(page){return normalizedClient.describeRumLogList({projectId:normalizedProjectId,startTime:startMs,endTime:period.endMs,orderBy:'asc',limit:RUM_LOG_PAGE_SIZE,page,query:`id:${normalizedProjectId} AND type:${type} AND env:${normalizedEnvironment}`});}
          function accept(result,page){
            if(result.page!==undefined&&result.page!==page)providerError('rum_pagination_mismatch','page');
            if(pagesRead&&result.total!==total)unstable=true;total=Math.max(total,result.total);pagesRead=Math.max(pagesRead,page);invalidRows+=result.invalidRows;fetchedRows+=result.records.length+result.invalidRows;
            records.push(...result.records.filter(r=>Date.parse(r.timestamp)>=startMs&&Date.parse(r.timestamp)<period.endMs));
          }
          accept(await fetchPage(1),1);
          for(let first=2;first<=Math.min(500,Math.ceil(total/RUM_LOG_PAGE_SIZE));first+=6){
            const pages=Array.from({length:Math.min(6,Math.min(500,Math.ceil(total/RUM_LOG_PAGE_SIZE))-first+1)},(_,i)=>first+i);
            const results=await Promise.all(pages.map(fetchPage));results.forEach((result,i)=>accept(result,pages[i]));
          }
          const truncated=fetchedRows<total,complete=!truncated&&!invalidRows&&!unstable&&startMs===period.startMs;

          return {records,coverage:{source:`tencent-rum-${type}-records`,status:complete?'complete':'partial',complete,start:new Date(startMs).toISOString(),end:new Date(period.endMs).toISOString(),requestedStart:new Date(period.startMs).toISOString(),retainedDays:RUM_RAW_LOG_RETENTION_DAYS,totalLogs:total,acceptedLogs:records.length,invalidLogs:invalidRows,pagesRead,truncated,unstable,rawIdentifiersReturned:false}};
        }
        const safe=async task=>{try{return {ok:true,value:await task()};}catch(error){return {ok:false,error:{code:error.code||'query_failed',action:error.action,providerCode:error.providerCode,requestId:error.requestId}};}};
        const [currentScores,previousScores,limits,dimensions,currentPages,previousPages,currentEvents,previousEvents]=await Promise.all([
          score(range.current),score(range.previous),normalizedClient.describeProjectLimits({projectId:normalizedProjectId}),
          Promise.all(['from','device','browser','platform','region'].map(type=>safe(()=>pageStatistics(range.current,type)))),
          safe(()=>logWindow(range.current,'pv')),safe(()=>logWindow(range.previous,'pv')),
          safe(()=>logWindow(range.current,'event')),safe(()=>logWindow(range.previous,'event')),
        ]);
        const currentScore=scoreForProject(currentScores,normalizedProjectId),previousScore=scoreForProject(previousScores,normalizedProjectId);
        function pageData(result,score){
          if(!result.ok)return {records:[],coverage:{status:'error',complete:false,error:result.error},aggregate:null};
          const value=result.value;
          // A successful empty query conflicting with an observed score is not a zero-traffic window.
          if(value.coverage.complete&&!value.records.length&&finiteNumber(score.PagePv)>0)value.coverage={...value.coverage,status:'mismatch',complete:false};
          return {...value,aggregate:aggregatePageRecords(value.records,value.coverage)};
        }
        const pageNow=pageData(currentPages,currentScore),pageBefore=pageData(previousPages,previousScore);
        function eventData(result){
          if(!result.ok)return {coverage:{status:'error',error:result.error,complete:false}};
          const value=result.value;
          const business=businessFromRumLogs(value.records,{...value.coverage,declaredCutover:normalizedBusinessCutoverMs!==null});
          return {...business,coverage:{...business.coverage,queryStatus:value.coverage.status,status:business.coverage.acceptedEvents?'observed':'not-observed',observedEventNames:[...new Set(value.records.filter(r=>RUM_BUSINESS_EVENT_NAMES.has(r.eventName)).map(r=>r.eventName))].sort(),reason:normalizedBusinessCutoverMs===null?'observed-schema-discovery':'declared-cutover'}};
        }
        const business=eventData(currentEvents),beforeBusiness=eventData(previousEvents);
        function observedMetrics(page,score,business){
          const observed=page.coverage.complete&&page.aggregate;
          const metrics=metricsFromScore(score);
          metrics.newVisitors=null;metrics.filteredRequests=null;metrics.conversions=business.coverage.status==='observed'?business.conversions:null;
          metrics.pv=observed?page.aggregate.pv:score.PagePv??null;
          metrics.uv=observed?page.aggregate.uv:score.PageUv??null;
          metrics.sessions=observed?page.aggregate.sessions:null;
          metrics.avgDurationSeconds=business.coverage.status==='observed'&&business.coverage.complete&&business.engagement?.sessions>0?business.engagement.avgDurationSeconds:null;
          return metrics;
        }
        const metrics=observedMetrics(pageNow,currentScore,business),previousMetrics=observedMetrics(pageBefore,previousScore,beforeBusiness);
        const measurementBasis=pageNow.coverage.complete?'rum-page-record-window':'rum-official-hour-aggregate';
        const previousBasis=pageBefore.coverage.complete?'rum-page-record-window':'rum-official-hour-aggregate';
        const comparisonAvailable=measurementBasis===previousBasis&&metrics.pv!==null&&previousMetrics.pv!==null&&metrics.uv!==null&&previousMetrics.uv!==null;
        const topPages=attachEngagement(pageNow.aggregate?.pages||[],business);
        const productionSources=dimensions[0].ok?sourcesFromV2(dimensions[0].value):[];
        const platforms=dimensions[3].ok?platformDimensions(dimensions[3].value):{platforms:[],devices:[]};
        const unavailableDimensions=['from','device','browser','platform','region'].filter((_,i)=>!dimensions[i].ok);
        const sessionCoverage=pageNow.coverage;
        const engagement=business.engagement?{...business.engagement,durationCoverage:sessionCoverage.complete&&metrics.sessions>0&&business.engagement.sessions<=metrics.sessions?business.engagement.sessions/metrics.sessions:null,durationKnownSessions:business.engagement.sessions,coverageDefinition:'engagement SDK sessions / observed page SDK sessions'}:null;
        return Object.freeze({dataSource:'tencent-rum',updatedAt:referenceNow.toISOString(),range:isoRange(range.current.startMs,range.current.endMs,{days:normalizedDays}),metrics,performance:performanceFromScore(currentScore),
          previousPeriod:{range:isoRange(range.previous.startMs,range.previous.endMs,{days:normalizedDays}),metrics:previousMetrics,performance:performanceFromScore(previousScore),measurementBasis:previousBasis,businessEventCoverage:beforeBusiness.coverage},
          trend:pageNow.aggregate?.trend||[],topPages,sources:pageNow.aggregate?.sources||[],previousPageSources:productionSources,devices:platforms.devices.length?platforms.devices:dimensions[1].ok?devicesFromV2(dimensions[1].value):[],
          browsers:dimensions[2].ok?dimensionsFromV2(dimensions[2].value,'browser'):[],operatingSystems:platforms.platforms,regions:dimensions[4].ok?dimensionsFromV2(dimensions[4].value,'region'):[],
          pagePaths:pageJourneysFromRumLogs(pageNow.records,pageNow.coverage),
          ...(business.coverage.status==='observed'?{cta:business.cta,businessActions:business.actions,wechatFunnel:{...business.wechatFunnel,steps:business.wechatFunnel.steps.filter(step=>business.coverage.observedEventNames.includes(step.eventName))},engagement}:{}),
          coverage:{currentAvailable:metrics.pv!==null,comparisonAvailable,current:pageNow.coverage,previous:pageBefore.coverage},
          dataQuality:{environment:normalizedEnvironment,measurementBasis,comparisonAvailable,trendEnvironmentScope:'production-rum-records',samplingRate:samplingInfo(limits).samplingRate,unavailableDimensions,pageJourneyCoverage:pageNow.coverage,sessionCoverage,businessEventCoverage:business.coverage,
            detail:'仅腾讯云 RUM production；全部接口统一截止到最近完整小时。页面与趋势由同一批 RUM 页面记录聚合；完整窗口按设备哈希去重 UV，不相加每日 UV。超出30天记录保留期时总量使用官方小时聚合，明细明确标注覆盖期；不同总量口径不显示环比。事件按已观测的规范自动查询，不以缺失配置跳过。渠道按窗口内 SDK 会话首条页面记录的引荐来源分类，跨渠道 UV 不合计；上一页面来源单独保留官方聚合。设备等为官方分类浏览量，其 UV 未返回时留空。',rawLogsReturned:false,dimensionErrors:Object.fromEntries(['from','device','browser','platform','region'].map((type,i)=>[type,dimensions[i].ok?null:dimensions[i].error]))}
        });
      });
    },

    async getRealtime({ minutes = 30, now = new Date() } = {}) {
      const normalizedMinutes = normalizeInteger(minutes, 'analytics_minutes', 5, 1440);
      const referenceNow = normalizeDate(now);
      const endMs = referenceNow.getTime();
      return cached(`realtime:${normalizedMinutes}`, endMs, async () => {
        const range = { startMs: endMs - normalizedMinutes * MINUTE_MS, endMs };
        const [pageViews, visitors, limits] = await Promise.all([
          pageStatistics(range, 'ckpv'),
          pageStatistics(range, 'ckuv'),
          normalizedClient.describeProjectLimits({ projectId: normalizedProjectId }),
        ]);
        const sampling = samplingInfo(limits);
        return Object.freeze({
          dataSource: 'tencent-rum',
          range: Object.freeze({
            minutes: normalizedMinutes,
            start: new Date(range.startMs).toISOString(),
            end: referenceNow.toISOString(),
          }),
          updatedAt: referenceNow.toISOString(),
          activeVisitors: aggregateCountFromV2(visitors, ['uv', 'ckuv', 'visitors', 'value']),
          pageViews: aggregateCountFromV2(pageViews, ['pv', 'ckpv', 'pageviews', 'value']),
          recentViews: Object.freeze([]),
          dataQuality: Object.freeze({
            samplingRate: sampling.samplingRate,
            rawLogsReturned: false,
            environment: normalizedEnvironment,
            detail:
              `当前窗口限定 env=${normalizedEnvironment}；` +
              'activeVisitors 是所选分钟窗口内的设备 UV，不代表此刻在线人数。',
          }),
        });
      });
    },
  });
}

export function createTencentRumAnalyticsProviderFromEnvironment(
  environment = process.env,
  { fetchImpl, clock } = {}
) {
  const config = readTencentRumEnvironmentConfig(environment);
  const client = createTencentRumClient({
    secretId: config.secretId,
    secretKey: config.secretKey,
    sessionToken: config.sessionToken,
    region: config.region,
    ...(fetchImpl === undefined ? {} : { fetchImpl }),
    ...(clock === undefined ? {} : { clock }),
  });
  return createTencentRumAnalyticsProvider({
    client,
    projectId: config.projectId,
    environment: config.environment,
    cacheMs: config.cacheMs,
    businessCutoverAt: config.businessCutoverAt,
    queryMinIntervalMs: 100,
  });
}

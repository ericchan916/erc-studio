import { createHash } from 'node:crypto';
import { signTencentCloudRequest } from './tc3-signature.mjs';

export const TENCENT_RUM_ENDPOINT = 'https://rum.tencentcloudapi.com/';
export const TENCENT_RUM_HOST = 'rum.tencentcloudapi.com';
export const TENCENT_RUM_SERVICE = 'rum';
export const TENCENT_RUM_VERSION = '2021-06-22';
export const TENCENT_RUM_ACTIONS = Object.freeze({
  scores: 'DescribeScoresV2',
  pv: 'DescribePvList',
  uv: 'DescribeUvList',
  projectLimits: 'DescribeProjectLimits',
  pageStatistics: 'DescribeDataPvUrlStatisticsV2',
  logs: 'DescribeRumLogList',
});

const JSON_CONTENT_TYPE = 'application/json; charset=utf-8';
const MAX_RESPONSE_BYTES = 2 * 1024 * 1024;
const MAX_V2_RESULTS = 100;
const MAX_V2_SERIES = 100;
const MAX_V2_COLUMNS = 128;
const MAX_V2_ROWS = 10_000;
const MAX_V2_CELLS = 100_000;
const RUM_SCROLL_BUCKETS = new Set([0, 25, 50, 75, 90]);
const CONFIG_FIELDS = new Set([
  'secretId',
  'secretKey',
  'sessionToken',
  'region',
  'fetchImpl',
  'fetch',
  'clock',
  'now',
]);
const SCORE_FIELDS = new Set(['startTime', 'endTime', 'projectIds', 'type', 'env']);
const LIST_FIELDS = new Set(['projectId', 'startTime', 'endTime', 'dimension']);
const LIMIT_FIELDS = new Set(['projectId']);
const PAGE_STATISTICS_FIELDS = new Set([
  'projectId',
  'startTime',
  'endTime',
  'type',
  'env',
  'groupByType',
  'granularity',
]);
const LOG_LIST_FIELDS = new Set([
  'projectId',
  'startTime',
  'endTime',
  'orderBy',
  'limit',
  'page',
  'query',
]);
const ENVIRONMENTS = new Set([
  'production',
  'prod',
  'development',
  'gray',
  'pre',
  'daily',
  'local',
  'test',
  'others',
]);
const PAGE_STATISTIC_TYPES = new Set([
  'allcount',
  'day',
  'vp',
  'ckuv',
  'ckpv',
  'condition',
  'pagepv',
  'nettype',
  'version',
  'platform',
  'isp',
  'region',
  'device',
  'browser',
  'ext1',
  'ext2',
  'ext3',
  'ret',
  'status',
  'from',
  'url',
  'env',
]);

/**
 * Error details are deliberately allowlisted. Tencent response messages and bodies are never
 * attached because they may echo request input or credentials.
 */
export class TencentRumError extends Error {
  constructor(code, { action, providerCode, requestId, statusCode, field, resultIndex, seriesIndex, rowIndex } = {}) {
    super(errorMessage(code));
    this.name = 'TencentRumError';
    this.code = code;
    this.provider = 'tencent-rum';
    if (action) this.action = action;
    if (providerCode) this.providerCode = providerCode;
    if (requestId) this.requestId = requestId;
    if (Number.isInteger(statusCode)) this.statusCode = statusCode;
    if (field) this.field = field;
    if (Number.isInteger(resultIndex)) this.resultIndex = resultIndex;
    if (Number.isInteger(seriesIndex)) this.seriesIndex = seriesIndex;
    if (Number.isInteger(rowIndex)) this.rowIndex = rowIndex;
  }

  toJSON() {
    return Object.fromEntries(
      [
        ['name', this.name],
        ['code', this.code],
        ['provider', this.provider],
        ['action', this.action],
        ['providerCode', this.providerCode],
        ['requestId', this.requestId],
        ['statusCode', this.statusCode],
        ['field', this.field],
        ['resultIndex', this.resultIndex],
        ['seriesIndex', this.seriesIndex],
        ['rowIndex', this.rowIndex],
      ].filter(([, value]) => value !== undefined)
    );
  }
}

function errorMessage(code) {
  switch (code) {
    case 'invalid_config':
      return 'Invalid Tencent RUM client configuration';
    case 'invalid_request':
      return 'Invalid Tencent RUM API request';
    case 'transport_error':
      return 'Tencent RUM request transport failed';
    case 'http_error':
      return 'Tencent RUM request returned an HTTP error';
    case 'api_error':
      return 'Tencent RUM API returned an error';
    case 'invalid_response':
      return 'Tencent RUM returned an invalid response';
    case 'invalid_v2_result':
      return 'Tencent RUM returned an invalid aggregate result';
    default:
      return 'Tencent RUM request failed';
  }
}

function isRecord(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function assertRecord(value, code, field) {
  if (!isRecord(value)) throw new TencentRumError(code, { field });
}

function assertKnownFields(value, allowedFields, code) {
  const unknown = Object.keys(value).find((field) => !allowedFields.has(field));
  // Do not reflect arbitrary keys because callers can accidentally use a secret as a key.
  if (unknown) throw new TencentRumError(code, { field: 'unknown' });
}

function assertCredential(value, field) {
  if (
    typeof value !== 'string' ||
    value.length < 1 ||
    value.length > 512 ||
    value.trim() !== value ||
    /[\u0000-\u001f\u007f]/.test(value)
  ) {
    throw new TencentRumError('invalid_config', { field });
  }
  return value;
}

function optionalCredential(value, field) {
  if (value === undefined || value === null || value === '') return '';
  return assertCredential(value, field);
}

function assertSingleAlias(config, primary, alias) {
  if (config[primary] !== undefined && config[alias] !== undefined) {
    throw new TencentRumError('invalid_config', { field: `${primary}/${alias}` });
  }
}

function normalizeRegion(value) {
  if (
    typeof value !== 'string' ||
    value.length < 3 ||
    value.length > 64 ||
    value.trim() !== value ||
    !/^[a-z][a-z0-9-]*$/.test(value)
  ) {
    throw new TencentRumError('invalid_config', { field: 'region' });
  }
  return value;
}

function normalizeFetch(fetchImpl) {
  if (typeof fetchImpl !== 'function') {
    throw new TencentRumError('invalid_config', { field: 'fetchImpl' });
  }
  return fetchImpl;
}

function normalizeClock(clock) {
  if (typeof clock !== 'function') {
    throw new TencentRumError('invalid_config', { field: 'clock' });
  }
  return () => {
    let value;
    try {
      value = clock();
    } catch {
      throw new TencentRumError('invalid_config', { field: 'clock' });
    }
    const timestampMs = value instanceof Date ? value.getTime() : Number(value);
    if (!Number.isFinite(timestampMs) || timestampMs < 0) {
      throw new TencentRumError('invalid_config', { field: 'clock' });
    }
    return Math.floor(timestampMs / 1000);
  };
}

function assertProjectId(value, field = 'projectId') {
  if (!Number.isSafeInteger(value) || value < 1 || value > 2_147_483_647) {
    throw new TencentRumError('invalid_request', { field });
  }
  return value;
}

function isValidUtcParts(year, month, day, hour = 0) {
  const date = new Date(Date.UTC(year, month - 1, day, hour));
  return (
    date.getUTCFullYear() === year &&
    date.getUTCMonth() === month - 1 &&
    date.getUTCDate() === day &&
    date.getUTCHours() === hour
  );
}

function assertTimeString(value, field, precision) {
  const pattern = precision === 'hour' ? /^(\d{4})(\d{2})(\d{2})(\d{2})$/ : /^(\d{4})(\d{2})(\d{2})$/;
  const match = typeof value === 'string' ? value.match(pattern) : null;
  if (!match) throw new TencentRumError('invalid_request', { field });
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = precision === 'hour' ? Number(match[4]) : 0;
  if (year < 2000 || year > 2200 || !isValidUtcParts(year, month, day, hour)) {
    throw new TencentRumError('invalid_request', { field });
  }
  return value;
}

function assertOrderedTimes(startTime, endTime, { allowEqual = false } = {}) {
  if (allowEqual ? startTime > endTime : startTime >= endTime) {
    throw new TencentRumError('invalid_request', { field: 'endTime' });
  }
}

function optionalEnvironment(value) {
  if (value === undefined || value === null || value === '') return '';
  if (typeof value !== 'string' || !ENVIRONMENTS.has(value)) {
    throw new TencentRumError('invalid_request', { field: 'env' });
  }
  return value;
}

function normalizeScoresRequest(input) {
  assertRecord(input, 'invalid_request', 'request');
  assertKnownFields(input, SCORE_FIELDS, 'invalid_request');
  const startTime = assertTimeString(input.startTime, 'startTime', 'hour');
  const endTime = assertTimeString(input.endTime, 'endTime', 'hour');
  assertOrderedTimes(startTime, endTime);

  const payload = { StartTime: startTime, EndTime: endTime };
  if (input.projectIds !== undefined) {
    if (!Array.isArray(input.projectIds) || input.projectIds.length < 1 || input.projectIds.length > 100) {
      throw new TencentRumError('invalid_request', { field: 'projectIds' });
    }
    const projectIds = input.projectIds.map((value) => assertProjectId(value, 'projectIds'));
    if (new Set(projectIds).size !== projectIds.length) {
      throw new TencentRumError('invalid_request', { field: 'projectIds' });
    }
    payload.IDList = projectIds;
  }
  if (input.type !== undefined && input.type !== '') {
    if (input.type !== 'hour' && input.type !== 'day') {
      throw new TencentRumError('invalid_request', { field: 'type' });
    }
    payload.Type = input.type;
  }
  const env = optionalEnvironment(input.env);
  if (env) payload.Env = env;
  return payload;
}

function normalizeListRequest(input) {
  assertRecord(input, 'invalid_request', 'request');
  assertKnownFields(input, LIST_FIELDS, 'invalid_request');
  const projectId = assertProjectId(input.projectId);
  const startTime = assertTimeString(input.startTime, 'startTime', 'day');
  const endTime = assertTimeString(input.endTime, 'endTime', 'day');
  assertOrderedTimes(startTime, endTime, { allowEqual: true });
  const payload = { ProjectId: projectId, EndTime: endTime, StartTime: startTime };
  if (input.dimension !== undefined && input.dimension !== '') {
    if (input.dimension !== 'd' && input.dimension !== 'm') {
      throw new TencentRumError('invalid_request', { field: 'dimension' });
    }
    payload.Dimension = input.dimension;
  }
  return payload;
}

function normalizeProjectLimitsRequest(input) {
  assertRecord(input, 'invalid_request', 'request');
  assertKnownFields(input, LIMIT_FIELDS, 'invalid_request');
  return { ProjectID: assertProjectId(input.projectId) };
}

function assertUnixSeconds(value, field) {
  if (!Number.isSafeInteger(value) || value < 0 || value > 9_999_999_999) {
    throw new TencentRumError('invalid_request', { field });
  }
  return value;
}

function normalizePageStatisticsRequest(input) {
  assertRecord(input, 'invalid_request', 'request');
  assertKnownFields(input, PAGE_STATISTICS_FIELDS, 'invalid_request');
  const startTime = assertUnixSeconds(input.startTime, 'startTime');
  const endTime = assertUnixSeconds(input.endTime, 'endTime');
  if (endTime <= startTime) throw new TencentRumError('invalid_request', { field: 'endTime' });
  if (typeof input.type !== 'string' || !PAGE_STATISTIC_TYPES.has(input.type)) {
    throw new TencentRumError('invalid_request', { field: 'type' });
  }

  const payload = {
    StartTime: startTime,
    Type: input.type,
    EndTime: endTime,
    ID: assertProjectId(input.projectId),
    IsNewData: 1,
  };
  const env = optionalEnvironment(input.env);
  if (env) payload.Env = env;
  if (input.groupByType !== undefined) {
    if (!Number.isInteger(input.groupByType) || input.groupByType < 1 || input.groupByType > 5) {
      throw new TencentRumError('invalid_request', { field: 'groupByType' });
    }
    payload.GroupByType = input.groupByType;
  }
  if (input.granularity !== undefined && input.granularity !== '') {
    if (!['1m', '5m', '30m', '1h', '1d'].includes(input.granularity)) {
      throw new TencentRumError('invalid_request', { field: 'granularity' });
    }
    payload.Granularity = input.granularity;
  }
  return payload;
}

function assertUnixMillis(value, field) {
  if (!Number.isSafeInteger(value) || value < 0 || value > 9_999_999_999_999) {
    throw new TencentRumError('invalid_request', { field });
  }
  return value;
}

function normalizeLogListRequest(input) {
  assertRecord(input, 'invalid_request', 'request');
  assertKnownFields(input, LOG_LIST_FIELDS, 'invalid_request');
  const startTime = assertUnixMillis(input.startTime, 'startTime');
  const endTime = assertUnixMillis(input.endTime, 'endTime');
  if (endTime <= startTime) throw new TencentRumError('invalid_request', { field: 'endTime' });
  const limit = Number(input.limit);
  const page = Number(input.page);
  if (!Number.isInteger(limit) || limit < 1 || limit > 100) {
    throw new TencentRumError('invalid_request', { field: 'limit' });
  }
  if (!Number.isInteger(page) || page < 1 || page > 100_000) {
    throw new TencentRumError('invalid_request', { field: 'page' });
  }
  if (input.orderBy !== 'asc' && input.orderBy !== 'desc') {
    throw new TencentRumError('invalid_request', { field: 'orderBy' });
  }
  if (
    typeof input.query !== 'string' ||
    input.query.length < 1 ||
    input.query.length > 4096 ||
    /[\u0000-\u001f\u007f]/.test(input.query)
  ) {
    throw new TencentRumError('invalid_request', { field: 'query' });
  }
  return {
    OrderBy: input.orderBy,
    StartTime: String(startTime),
    Limit: limit,
    Page: page,
    Query: input.query,
    EndTime: String(endTime),
    ID: assertProjectId(input.projectId),
  };
}

function containsCredential(value, credentials) {
  return credentials.some((credential) => credential && value.includes(credential));
}

function safeProviderCode(value, credentials) {
  return typeof value === 'string' &&
    /^[A-Za-z0-9._-]{1,128}$/.test(value) &&
    !containsCredential(value, credentials)
    ? value
    : 'TencentCloud.UnknownError';
}

function safeRequestId(value, credentials = []) {
  return typeof value === 'string' &&
    /^[A-Za-z0-9-]{1,128}$/.test(value) &&
    !containsCredential(value, credentials)
    ? value
    : undefined;
}

function responseStatus(response) {
  return Number.isInteger(response?.status) ? response.status : undefined;
}

async function parseResponseBody(response, action) {
  if (!response || typeof response.text !== 'function') {
    throw new TencentRumError('invalid_response', { action, statusCode: responseStatus(response) });
  }
  let text;
  try {
    text = await response.text();
  } catch {
    throw new TencentRumError('invalid_response', { action, statusCode: responseStatus(response) });
  }
  if (typeof text !== 'string' || Buffer.byteLength(text, 'utf8') > MAX_RESPONSE_BYTES) {
    throw new TencentRumError('invalid_response', { action, statusCode: responseStatus(response) });
  }
  let body;
  try {
    body = JSON.parse(text);
  } catch {
    throw new TencentRumError('invalid_response', { action, statusCode: responseStatus(response) });
  }
  if (!isRecord(body)) {
    throw new TencentRumError('invalid_response', { action, statusCode: responseStatus(response) });
  }
  return body;
}

function safeFiniteNumber(value, { integer = false, min = 0 } = {}) {
  if (value === null || value === undefined) return null;
  if (typeof value !== 'number' || !Number.isFinite(value) || value < min || (integer && !Number.isInteger(value))) {
    return undefined;
  }
  return value;
}

const SCORE_NUMBER_FIELDS = Object.freeze({
  ProjectID: { integer: true, min: 1 },
  Score: {},
  ApiPerformanceScore: {},
  ApiAvailableScore: {},
  ApiNum: { integer: true },
  ApiFail: { integer: true },
  ApiDuration: {},
  PagePerformanceScore: {},
  PagePv: { integer: true },
  PageUv: { integer: true },
  PageError: { integer: true },
  PageDuration: {},
  PageLCP: {},
  PageFID: {},
  PageCLS: {},
  PageFCP: {},
  PageINP: {},
  JsErrorScore: {},
  StaticAvailableScore: {},
  StaticPerformanceScore: {},
  StaticNum: { integer: true },
  StaticFail: { integer: true },
  StaticDuration: {},
});

function normalizeScoreSet(response, action, statusCode, credentials) {
  const source = response.ScoreSet ?? [];
  if (!Array.isArray(source) || source.length > 10_000) {
    throw new TencentRumError('invalid_response', {
      action,
      statusCode,
      requestId: safeRequestId(response.RequestId, credentials),
    });
  }
  const scores = source.map((record) => {
    if (!isRecord(record)) throw new TencentRumError('invalid_response', { action, statusCode });
    const score = {};
    for (const [field, rules] of Object.entries(SCORE_NUMBER_FIELDS)) {
      const value = safeFiniteNumber(record[field], rules);
      if (value === undefined) {
        throw new TencentRumError('invalid_response', { action, statusCode, field });
      }
      if (value !== null) score[field] = value;
    }
    if (!Number.isInteger(score.ProjectID)) {
      throw new TencentRumError('invalid_response', { action, statusCode, field: 'ProjectID' });
    }
    // Deliberately omit Exclusion because it can contain raw error messages.
    return Object.freeze(score);
  });
  return Object.freeze({
    ScoreSet: Object.freeze(scores),
    RequestId: safeRequestId(response.RequestId, credentials),
  });
}

function safeTimeLabel(value) {
  return typeof value === 'string' && /^[0-9T:+ Z.-]{1,40}$/.test(value) ? value : undefined;
}

function normalizeMetricList(response, action, statusCode, credentials, setName, valueName) {
  const source = response[setName] ?? [];
  if (!Array.isArray(source) || source.length > 100_000) {
    throw new TencentRumError('invalid_response', { action, statusCode });
  }
  const records = source.map((record) => {
    if (!isRecord(record)) throw new TencentRumError('invalid_response', { action, statusCode });
    const projectId = safeFiniteNumber(record.ProjectId, { integer: true, min: 1 });
    const metric = record[valueName];
    const createTime = safeTimeLabel(record.CreateTime);
    if (
      projectId === undefined ||
      projectId === null ||
      typeof metric !== 'string' ||
      !/^\d{1,30}$/.test(metric) ||
      !createTime
    ) {
      throw new TencentRumError('invalid_response', { action, statusCode });
    }
    return Object.freeze({ ProjectId: projectId, [valueName]: metric, CreateTime: createTime });
  });
  return Object.freeze({
    [setName]: Object.freeze(records),
    RequestId: safeRequestId(response.RequestId, credentials),
  });
}

function normalizeProjectLimits(response, action, statusCode, credentials) {
  const source = response.ProjectLimitSet ?? [];
  if (!Array.isArray(source) || source.length > 10_000) {
    throw new TencentRumError('invalid_response', { action, statusCode });
  }
  const records = source.map((record) => {
    if (!isRecord(record)) throw new TencentRumError('invalid_response', { action, statusCode });
    const id = safeFiniteNumber(record.ID, { integer: true });
    const projectId = safeFiniteNumber(record.ProjectID, { integer: true, min: 1 });
    const reportRate = safeFiniteNumber(record.ReportRate, { integer: true });
    const reportType = safeFiniteNumber(record.ReportType, { integer: true, min: 1 });
    if (
      id === undefined ||
      projectId === undefined ||
      projectId === null ||
      reportRate === undefined ||
      reportRate === null ||
      reportType === undefined ||
      reportType === null ||
      typeof record.ProjectInterface !== 'string' ||
      !/^[A-Za-z0-9._-]{1,64}$/.test(record.ProjectInterface)
    ) {
      throw new TencentRumError('invalid_response', { action, statusCode });
    }
    return Object.freeze({
      ...(id === null ? {} : { ID: id }),
      ProjectID: projectId,
      ProjectInterface: record.ProjectInterface,
      ReportRate: reportRate,
      ReportType: reportType,
    });
  });
  return Object.freeze({
    ProjectLimitSet: Object.freeze(records),
    RequestId: safeRequestId(response.RequestId, credentials),
  });
}

function safeOpaqueHash(scope, value) {
  if (
    typeof value !== 'string' ||
    value.length < 8 ||
    value.length > 512 ||
    /[\u0000-\u001f\u007f]/.test(value)
  ) {
    return '';
  }
  return createHash('sha256').update(`${scope}\0${value}`).digest('hex');
}

function sanitizeRumPath(value) {
  if (
    typeof value !== 'string' ||
    value.length < 1 ||
    value.length > 2048 ||
    /[\u0000-\u001f\u007f]/.test(value)
  ) {
    return '';
  }
  let candidate = value.trim();
  try {
    if (/^https?:\/\//i.test(candidate)) {
      candidate = new URL(candidate).pathname;
    } else {
      candidate = candidate.split(/[?#]/, 1)[0];
      if (!candidate.startsWith('/')) {
        const slash = candidate.indexOf('/');
        const leading = slash >= 0 ? candidate.slice(0, slash) : candidate;
        candidate = /\./.test(leading) ? (slash >= 0 ? candidate.slice(slash) : '/') : `/${candidate}`;
      }
    }
  } catch {
    return '';
  }
  const sanitized = candidate
    .split(/[?#]/, 1)[0]
    .split('/')
    .map((segment) => {
      if (!segment) return segment;
      let decoded;
      try {
        decoded = decodeURIComponent(segment);
      } catch {
        return ':redacted';
      }
      const sensitive =
        !/^evt_[a-f0-9]{20}$/i.test(decoded) && (
        /@/.test(decoded) ||
        /(?:token|secret|password|passwd|authorization|bearer|sessionid)/i.test(decoded) ||
        /\b1\d{10}\b/.test(decoded) ||
        /\b\d{12,}\b/.test(decoded) ||
        (/^[A-Za-z0-9_-]{24,}$/.test(decoded) && /[A-Za-z]/.test(decoded) && /\d/.test(decoded))
        );
      return sensitive ? ':redacted' : segment;
    })
    .join('/')
    .replace(/\/{2,}/g, '/')
    .slice(0, 300);
  return sanitized.startsWith('/') ? sanitized || '/' : '';
}

function trafficSourceFromRumRow(row) {
  const referrer = typeof row.referer === 'string' ? row.referer : '';
  if (!referrer) return 'direct';
  let hostname;
  try {
    hostname = new URL(referrer).hostname.toLowerCase();
  } catch {
    return 'unknown';
  }
  if (hostname === 'example.invalid' || hostname.endsWith('.example.invalid')) return 'internal';
  if (/(^|\.)(baidu|google|bing|sogou|so|sm)\./.test(hostname)) return 'organic';
  if (/(^|\.)(weixin|wechat|feishu|xiaohongshu|douyin|tiktok|x|twitter)\./.test(hostname) || hostname === 't.co') {
    return 'social';
  }
  return 'referral';
}

function safeRumDimension(value, maxLength = 64) {
  if (typeof value !== 'string' || value.length < 1 || value.length > maxLength) return '';
  const normalized = value.trim().toLowerCase();
  return /^[a-z0-9][a-z0-9._~-]*$/.test(normalized) ? normalized : '';
}

function safeRumInteger(value, { min = 0, max = Number.MAX_SAFE_INTEGER } = {}) {
  const number = Number(value);
  return Number.isSafeInteger(number) && number >= min && number <= max ? number : undefined;
}

function normalizeRumLogList(response, action, statusCode, credentials) {
  if (
    typeof response.Result !== 'string' ||
    Buffer.byteLength(response.Result, 'utf8') > MAX_RESPONSE_BYTES
  ) {
    throw new TencentRumError('invalid_response', { action, statusCode, field: 'Result' });
  }
  let decoded;
  try {
    decoded = JSON.parse(response.Result);
  } catch {
    throw new TencentRumError('invalid_response', { action, statusCode, field: 'Result' });
  }
  if (!isRecord(decoded) || decoded.code !== 0 || !isRecord(decoded.data)) {
    throw new TencentRumError('invalid_response', { action, statusCode, field: 'data' });
  }
  const { data } = decoded;
  const total = safeFiniteNumber(data.total, { integer: true });
  const limit = safeFiniteNumber(data.limit, { integer: true, min: 1 });
  const page = safeFiniteNumber(data.page, { integer: true, min: 1 });
  if (total === undefined || total === null || limit === undefined || limit === null || page === undefined || page === null) {
    throw new TencentRumError('invalid_response', { action, statusCode, field: 'pagination' });
  }
  const sourceRows = data.list === undefined || data.list === null ? [] : data.list;
  if (!Array.isArray(sourceRows) || sourceRows.length > 100) {
    throw new TencentRumError('invalid_response', { action, statusCode, field: 'list' });
  }

  let invalidRows = 0;
  const records = [];
  for (const row of sourceRows) {
    if (!isRecord(row)) {
      invalidRows += 1;
      continue;
    }
    if (row.type !== 'event' && row.type !== 'pv') {
      invalidRows += 1;
      continue;
    }
    const timestampMs = Number(row.date);
    const eventName = row.type === 'event' ? safeRumDimension(row.name || row.msg, 80) : '';
    const engagementEvent = eventName === 'engagement' || eventName === 'session_end';
    const activeMs = engagementEvent
      ? safeRumInteger(row.ext1, { max: 4 * 60 * 60 * 1000 })
      : 0;
    const scrollBucket = engagementEvent
      ? safeRumInteger(row.ext2, { max: 100 })
      : 0;
    if (
      (!eventName && row.type === 'event') ||
      (engagementEvent &&
        (activeMs === undefined || !RUM_SCROLL_BUCKETS.has(scrollBucket)))
    ) {
      invalidRows += 1;
      continue;
    }
    const path = sanitizeRumPath(
      row.type === 'event' ? row.ext3 || row.url || row.originFrom || '' : row.url || row.originFrom || ''
    );
    const sessionHash = safeOpaqueHash('session', row.sessionId || '');
    const visitorHash = safeOpaqueHash('visitor', row.aid || '');
    if (!Number.isSafeInteger(timestampMs) || timestampMs < 0 || !path || !sessionHash || !visitorHash) {
      invalidRows += 1;
      continue;
    }
    const previousPath = sanitizeRumPath(row.from || '');
    records.push(Object.freeze({
      timestamp: new Date(timestampMs).toISOString(),
      sessionHash,
      visitorHash,
      path,
      previousPath,
      source: trafficSourceFromRumRow(row),
      logType: row.type === 'event' ? 'event' : 'pv',
      eventName,
      ctaId: row.type === 'event' ? safeRumDimension(row.ext1) : '',
      placement: row.type === 'event' ? safeRumDimension(row.ext2) : '',
      activeMs,
      scrollBucket,
    }));
  }
  return Object.freeze({
    total,
    limit,
    page,
    invalidRows,
    records: Object.freeze(records),
    RequestId: safeRequestId(response.RequestId, credentials),
  });
}

function invalidV2(details = {}) {
  throw new TencentRumError('invalid_v2_result', details);
}

function safeV2String(value, maxLength, details) {
  if (
    typeof value !== 'string' ||
    value.length > maxLength ||
    /[\u0000-\u001f\u007f]/.test(value)
  ) {
    invalidV2(details);
  }
  return value;
}

function normalizeV2Cell(value, details) {
  if (value === null || typeof value === 'boolean') return value;
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') return safeV2String(value, 4096, details);
  invalidV2(details);
}

/**
 * Parse the JSON string returned by Tencent's DescribeData*V2 APIs. Rows are mapped by the
 * response-provided columns, so callers never depend on undocumented column order.
 */
export function parseTencentRumV2Result(value) {
  if (typeof value !== 'string' || value.length < 2 || Buffer.byteLength(value, 'utf8') > MAX_RESPONSE_BYTES) {
    invalidV2({ field: 'Result' });
  }
  let decoded;
  try {
    decoded = JSON.parse(value);
  } catch {
    invalidV2({ field: 'Result' });
  }
  if (!isRecord(decoded) || !Array.isArray(decoded.results) || decoded.results.length > MAX_V2_RESULTS) {
    invalidV2({ field: 'results' });
  }

  let cellCount = 0;
  const results = decoded.results.map((entry, resultIndex) => {
    const resultDetails = { resultIndex };
    if (!isRecord(entry)) invalidV2(resultDetails);
    const statementId = entry.statement_id;
    if (statementId !== undefined && (!Number.isSafeInteger(statementId) || statementId < 0)) {
      invalidV2({ ...resultDetails, field: 'statement_id' });
    }
    const total = entry.total;
    if (total !== undefined && (typeof total !== 'number' || !Number.isFinite(total) || total < 0)) {
      invalidV2({ ...resultDetails, field: 'total' });
    }
    const offset = entry.offset;
    if (offset !== undefined && offset !== null) safeV2String(offset, 512, { ...resultDetails, field: 'offset' });
    const rawSeries = entry.series ?? [];
    if (!Array.isArray(rawSeries) || rawSeries.length > MAX_V2_SERIES) {
      invalidV2({ ...resultDetails, field: 'series' });
    }

    const series = rawSeries.map((item, seriesIndex) => {
      const details = { resultIndex, seriesIndex };
      if (!isRecord(item) || !Array.isArray(item.columns) || !Array.isArray(item.values)) {
        invalidV2(details);
      }
      if (item.columns.length < 1 || item.columns.length > MAX_V2_COLUMNS || item.values.length > MAX_V2_ROWS) {
        invalidV2(details);
      }
      const columns = item.columns.map((column) => {
        const normalized = safeV2String(column, 128, { ...details, field: 'columns' });
        if (!normalized || ['__proto__', 'prototype', 'constructor'].includes(normalized)) {
          invalidV2({ ...details, field: 'columns' });
        }
        return normalized;
      });
      if (new Set(columns).size !== columns.length) invalidV2({ ...details, field: 'columns' });
      cellCount += columns.length * item.values.length;
      if (cellCount > MAX_V2_CELLS) invalidV2({ ...details, field: 'values' });

      const rows = item.values.map((row, rowIndex) => {
        const rowDetails = { ...details, rowIndex };
        if (!Array.isArray(row) || row.length !== columns.length) invalidV2(rowDetails);
        const output = {};
        for (let index = 0; index < columns.length; index += 1) {
          output[columns[index]] = normalizeV2Cell(row[index], rowDetails);
        }
        return Object.freeze(output);
      });
      const name = item.name === undefined || item.name === null
        ? ''
        : safeV2String(item.name, 256, { ...details, field: 'name' });
      // Tencent's real dimension labels live in series.tags, not in the numeric row.
      const tags = {};
      if (item.tags != null) {
        if (!isRecord(item.tags)) invalidV2({ ...details, field: 'tags' });
        const dimensions = new Set(['url','from','device','browser','platform','region','isp','nettype','version','env','os']);
        for (const [key, value] of Object.entries(item.tags)) {
          if (!dimensions.has(key.toLowerCase())) continue;
          tags[key] = safeV2String(value, 4096, { ...details, field: 'tags' });
        }
      }
      return Object.freeze({
        name,
        columns: Object.freeze(columns),
        ...(Object.keys(tags).length ? { tags: Object.freeze(tags) } : {}),
        rows: Object.freeze(rows),
      });
    });
    return Object.freeze({
      ...(statementId === undefined ? {} : { statementId }),
      ...(total === undefined ? {} : { total }),
      ...(offset === undefined || offset === null ? {} : { offset }),
      series: Object.freeze(series),
    });
  });

  return Object.freeze({
    requestId: safeRequestId(decoded.request_id),
    results: Object.freeze(results),
  });
}

export function createTencentRumClient(config) {
  assertRecord(config, 'invalid_config', 'config');
  assertKnownFields(config, CONFIG_FIELDS, 'invalid_config');
  assertSingleAlias(config, 'fetchImpl', 'fetch');
  assertSingleAlias(config, 'clock', 'now');

  const secretId = assertCredential(config.secretId, 'secretId');
  const secretKey = assertCredential(config.secretKey, 'secretKey');
  const sessionToken = optionalCredential(config.sessionToken, 'sessionToken');
  const region = normalizeRegion(config.region);
  const fetchImpl = normalizeFetch(config.fetchImpl ?? config.fetch ?? globalThis.fetch);
  const getTimestamp = normalizeClock(config.clock ?? config.now ?? Date.now);
  const credentials = [secretId, secretKey, sessionToken];

  async function request(action, payload, responseParser) {
    const body = JSON.stringify(payload);
    const timestamp = getTimestamp();
    let authorization;
    try {
      ({ authorization } = signTencentCloudRequest({
        secretId,
        secretKey,
        payload: body,
        timestamp,
        host: TENCENT_RUM_HOST,
        service: TENCENT_RUM_SERVICE,
      }));
    } catch {
      throw new TencentRumError('invalid_config', { field: 'signature' });
    }
    const headers = {
      Authorization: authorization,
      'Content-Type': JSON_CONTENT_TYPE,
      Host: TENCENT_RUM_HOST,
      'X-TC-Action': action,
      'X-TC-Timestamp': String(timestamp),
      'X-TC-Version': TENCENT_RUM_VERSION,
      'X-TC-Region': region,
    };
    if (sessionToken) headers['X-TC-Token'] = sessionToken;

    let httpResponse;
    try {
      httpResponse = await fetchImpl(TENCENT_RUM_ENDPOINT, { method: 'POST', headers, body, signal: AbortSignal.timeout(30_000) });
    } catch {
      throw new TencentRumError('transport_error', { action });
    }
    const statusCode = responseStatus(httpResponse);
    const responseBody = await parseResponseBody(httpResponse, action);
    const response = isRecord(responseBody.Response) ? responseBody.Response : null;
    const errorValue = response?.Error ?? responseBody.Error;
    if (errorValue !== undefined && errorValue !== null) {
      const providerError = isRecord(errorValue) ? errorValue : {};
      throw new TencentRumError('api_error', {
        action,
        providerCode: safeProviderCode(providerError.Code, credentials),
        requestId: safeRequestId(response?.RequestId, credentials),
        statusCode,
      });
    }
    if (httpResponse.ok === false || (statusCode !== undefined && statusCode >= 400)) {
      throw new TencentRumError('http_error', {
        action,
        requestId: safeRequestId(response?.RequestId, credentials),
        statusCode,
      });
    }
    if (!response) throw new TencentRumError('invalid_response', { action, statusCode });
    return responseParser(response, action, statusCode, credentials);
  }

  return Object.freeze({
    region,
    async describeScoresV2(input) {
      return request(TENCENT_RUM_ACTIONS.scores, normalizeScoresRequest(input), normalizeScoreSet);
    },
    async describePvList(input) {
      return request(TENCENT_RUM_ACTIONS.pv, normalizeListRequest(input), (response, action, statusCode, values) =>
        normalizeMetricList(response, action, statusCode, values, 'ProjectPvSet', 'Pv')
      );
    },
    async describeUvList(input) {
      return request(TENCENT_RUM_ACTIONS.uv, normalizeListRequest(input), (response, action, statusCode, values) =>
        normalizeMetricList(response, action, statusCode, values, 'ProjectUvSet', 'Uv')
      );
    },
    async describeProjectLimits(input) {
      return request(
        TENCENT_RUM_ACTIONS.projectLimits,
        normalizeProjectLimitsRequest(input),
        normalizeProjectLimits
      );
    },
    async describeDataPvUrlStatisticsV2(input) {
      return request(
        TENCENT_RUM_ACTIONS.pageStatistics,
        normalizePageStatisticsRequest(input),
        (response, action, statusCode, values) => {
          if (typeof response.Result !== 'string') {
            throw new TencentRumError('invalid_response', {
              action,
              statusCode,
              requestId: safeRequestId(response.RequestId, values),
              field: 'Result',
            });
          }
          let result;
          try {
            result = parseTencentRumV2Result(response.Result);
          } catch (error) {
            if (error instanceof TencentRumError) {
              error.action = action;
              if (!error.requestId) error.requestId = safeRequestId(response.RequestId, values);
              if (error.statusCode === undefined && statusCode !== undefined) error.statusCode = statusCode;
            }
            throw error;
          }
          return Object.freeze({
            Result: result,
            RequestId: safeRequestId(response.RequestId, values),
          });
        }
      );
    },
    async describeRumLogList(input) {
      return request(
        TENCENT_RUM_ACTIONS.logs,
        normalizeLogListRequest(input),
        normalizeRumLogList
      );
    },
  });
}

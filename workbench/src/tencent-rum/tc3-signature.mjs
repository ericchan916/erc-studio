import { createHash, createHmac } from 'node:crypto';

export const TENCENT_CLS_ENDPOINT = 'https://cls.tencentcloudapi.com/';
export const TENCENT_CLS_ACTION = 'SearchLog';
export const TENCENT_CLS_VERSION = '2020-10-16';

const TENCENT_CLS_HOST = 'cls.tencentcloudapi.com';
const TENCENT_CLS_SERVICE = 'cls';
const TC3_ALGORITHM = 'TC3-HMAC-SHA256';
const TC3_SIGNED_HEADERS = 'content-type;host';
const JSON_CONTENT_TYPE = 'application/json; charset=utf-8';
const MAX_QUERY_BYTES = 12 * 1024;
const CONFIG_FIELDS = new Set([
  'secretId',
  'secretKey',
  'sessionToken',
  'fetchImpl',
  'fetch',
  'clock',
  'now',
]);
const SEARCH_FIELDS = new Set(['region', 'topic', 'from', 'to', 'query']);

/**
 * A deliberately small error surface for CLS. Provider messages and response bodies are
 * never attached because either may echo request data. `providerCode` and `requestId` are
 * allowlist-sanitized before exposure.
 */
export class TencentClsError extends Error {
  constructor(code, { providerCode, requestId, statusCode, field, recordIndex } = {}) {
    super(errorMessage(code));
    this.name = 'TencentClsError';
    this.code = code;
    this.provider = 'tencent-cls';
    if (providerCode) this.providerCode = providerCode;
    if (requestId) this.requestId = requestId;
    if (Number.isInteger(statusCode)) this.statusCode = statusCode;
    if (field) this.field = field;
    if (Number.isInteger(recordIndex)) this.recordIndex = recordIndex;
  }

  toJSON() {
    return Object.fromEntries(
      [
        ['name', this.name],
        ['code', this.code],
        ['provider', this.provider],
        ['providerCode', this.providerCode],
        ['requestId', this.requestId],
        ['statusCode', this.statusCode],
        ['field', this.field],
        ['recordIndex', this.recordIndex],
      ].filter(([, value]) => value !== undefined)
    );
  }
}

function errorMessage(code) {
  switch (code) {
    case 'invalid_config':
      return 'Invalid Tencent CLS client configuration';
    case 'invalid_request':
      return 'Invalid Tencent CLS SearchLog request';
    case 'transport_error':
      return 'Tencent CLS request transport failed';
    case 'http_error':
      return 'Tencent CLS request returned an HTTP error';
    case 'api_error':
      return 'Tencent CLS API returned an error';
    case 'invalid_response':
      return 'Tencent CLS returned an invalid response';
    case 'invalid_analysis_record':
      return 'Tencent CLS returned an invalid analysis record';
    default:
      return 'Tencent CLS request failed';
  }
}

function isRecord(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function assertRecord(value, code, field) {
  if (!isRecord(value)) throw new TencentClsError(code, { field });
}

function assertKnownFields(value, allowedFields, code) {
  const unknownField = Object.keys(value).find((field) => !allowedFields.has(field));
  // Do not reflect an arbitrary object key: callers sometimes accidentally use a secret as
  // a key while assembling configuration dynamically.
  if (unknownField) throw new TencentClsError(code, { field: 'unknown' });
}

function assertCredential(value, field) {
  if (
    typeof value !== 'string' ||
    value.length < 1 ||
    value.length > 512 ||
    value.trim() !== value ||
    /[\u0000-\u001f\u007f]/.test(value)
  ) {
    throw new TencentClsError('invalid_config', { field });
  }
  return value;
}

function optionalCredential(value, field) {
  if (value === undefined || value === null || value === '') return '';
  return assertCredential(value, field);
}

function assertSingleAlias(config, primary, alias) {
  if (config[primary] !== undefined && config[alias] !== undefined) {
    throw new TencentClsError('invalid_config', { field: `${primary}/${alias}` });
  }
}

function normalizeClock(clock) {
  if (typeof clock !== 'function') {
    throw new TencentClsError('invalid_config', { field: 'clock' });
  }
  return () => {
    let value;
    try {
      value = clock();
    } catch {
      throw new TencentClsError('invalid_config', { field: 'clock' });
    }
    const timestampMs = value instanceof Date ? value.getTime() : Number(value);
    if (!Number.isFinite(timestampMs) || timestampMs < 0) {
      throw new TencentClsError('invalid_config', { field: 'clock' });
    }
    return Math.floor(timestampMs / 1000);
  };
}

function normalizeFetch(fetchImpl) {
  if (typeof fetchImpl !== 'function') {
    throw new TencentClsError('invalid_config', { field: 'fetchImpl' });
  }
  return fetchImpl;
}

function utcDate(timestampSeconds) {
  const date = new Date(timestampSeconds * 1000);
  if (Number.isNaN(date.getTime())) {
    throw new TencentClsError('invalid_config', { field: 'timestamp' });
  }
  return date.toISOString().slice(0, 10);
}

function sha256Hex(value) {
  return createHash('sha256').update(value, 'utf8').digest('hex');
}

function hmacSha256(key, value, encoding) {
  return createHmac('sha256', key).update(value, 'utf8').digest(encoding);
}

/**
 * Build a TC3-HMAC-SHA256 Authorization value. The generic host/service arguments exist so
 * the implementation can be checked against Tencent Cloud's published CVM signature vector;
 * the SearchLog client always calls this with the fixed CLS host and service.
 */
export function signTencentCloudRequest({
  secretId,
  secretKey,
  payload,
  timestamp,
  host,
  service,
  contentType = JSON_CONTENT_TYPE,
}) {
  const normalizedSecretId = assertCredential(secretId, 'secretId');
  const normalizedSecretKey = assertCredential(secretKey, 'secretKey');
  if (typeof payload !== 'string') {
    throw new TencentClsError('invalid_config', { field: 'payload' });
  }
  if (!Number.isSafeInteger(timestamp) || timestamp < 0) {
    throw new TencentClsError('invalid_config', { field: 'timestamp' });
  }
  if (typeof host !== 'string' || !/^[a-z0-9.-]+$/.test(host)) {
    throw new TencentClsError('invalid_config', { field: 'host' });
  }
  if (typeof service !== 'string' || !/^[a-z0-9-]+$/.test(service)) {
    throw new TencentClsError('invalid_config', { field: 'service' });
  }
  if (typeof contentType !== 'string' || contentType !== contentType.trim()) {
    throw new TencentClsError('invalid_config', { field: 'contentType' });
  }

  const date = utcDate(timestamp);
  const canonicalHeaders = `content-type:${contentType}\nhost:${host}\n`;
  const canonicalRequest = [
    'POST',
    '/',
    '',
    canonicalHeaders,
    TC3_SIGNED_HEADERS,
    sha256Hex(payload),
  ].join('\n');
  const credentialScope = `${date}/${service}/tc3_request`;
  const stringToSign = [
    TC3_ALGORITHM,
    String(timestamp),
    credentialScope,
    sha256Hex(canonicalRequest),
  ].join('\n');

  const secretDate = hmacSha256(`TC3${normalizedSecretKey}`, date);
  const secretService = hmacSha256(secretDate, service);
  const secretSigning = hmacSha256(secretService, 'tc3_request');
  const signature = hmacSha256(secretSigning, stringToSign, 'hex');
  const authorization =
    `${TC3_ALGORITHM} Credential=${normalizedSecretId}/${credentialScope}, ` +
    `SignedHeaders=${TC3_SIGNED_HEADERS}, Signature=${signature}`;

  return { authorization, signature, credentialScope };
}
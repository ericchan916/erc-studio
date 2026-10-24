import { createHash, createHmac } from 'node:crypto';
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  TENCENT_RUM_ACTIONS,
  TENCENT_RUM_ENDPOINT,
  TENCENT_RUM_HOST,
  TENCENT_RUM_VERSION,
  TencentRumError,
  createTencentRumClient,
  parseTencentRumV2Result,
} from '../src/tencent-rum/tencentRumClient.mjs';

const SECRET_ID = 'YOUR_TC3_TEST_ID';
const SECRET_KEY = 'YOUR_TC3_TEST_KEY';
const SESSION_TOKEN = 'temporary-rum-token-example';
const FIXED_TIMESTAMP = 1_787_990_400;
const FIXED_NOW = new Date(FIXED_TIMESTAMP * 1000);

function jsonResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async text() {
      return JSON.stringify(body);
    },
  };
}

function independentAuthorization({ body, action }) {
  const contentType = 'application/json; charset=utf-8';
  const date = new Date(FIXED_TIMESTAMP * 1000).toISOString().slice(0, 10);
  const sha256 = (value) => createHash('sha256').update(value, 'utf8').digest('hex');
  const hmac = (key, value, encoding) =>
    createHmac('sha256', key).update(value, 'utf8').digest(encoding);
  const canonicalRequest = [
    'POST',
    '/',
    '',
    `content-type:${contentType}\nhost:${TENCENT_RUM_HOST}\n`,
    'content-type;host',
    sha256(body),
  ].join('\n');
  const scope = `${date}/rum/tc3_request`;
  const stringToSign = [
    'TC3-HMAC-SHA256',
    String(FIXED_TIMESTAMP),
    scope,
    sha256(canonicalRequest),
  ].join('\n');
  const secretDate = hmac(`TC3${SECRET_KEY}`, date);
  const secretService = hmac(secretDate, 'rum');
  const secretSigning = hmac(secretService, 'tc3_request');
  const signature = hmac(secretSigning, stringToSign, 'hex');
  assert.equal(action, TENCENT_RUM_ACTIONS.scores);
  return (
    `TC3-HMAC-SHA256 Credential=${SECRET_ID}/${scope}, ` +
    `SignedHeaders=content-type;host, Signature=${signature}`
  );
}

function completeScore(overrides = {}) {
  return {
    ProjectID: 120000,
    Score: 98.5,
    ApiPerformanceScore: 96,
    ApiAvailableScore: 99,
    ApiNum: 40,
    ApiFail: 1,
    ApiDuration: 118.5,
    PagePerformanceScore: 92,
    PagePv: 42,
    PageUv: 17,
    PageError: 2,
    PageDuration: 860.5,
    PageLCP: 1200.5,
    PageFID: 12.5,
    PageCLS: 0.03,
    PageFCP: 410.5,
    PageINP: 85.5,
    JsErrorScore: 95,
    StaticAvailableScore: 99,
    StaticPerformanceScore: 97,
    StaticNum: 100,
    StaticFail: 2,
    StaticDuration: 35.5,
    ...overrides,
  };
}

test('DescribeScoresV2 sends the official TC3 POST shape and only returns typed aggregate fields', async () => {
  const calls = [];
  const client = createTencentRumClient({
    secretId: SECRET_ID,
    secretKey: SECRET_KEY,
    sessionToken: SESSION_TOKEN,
    region: 'ap-guangzhou',
    clock: () => FIXED_NOW,
    fetchImpl: async (...args) => {
      calls.push(args);
      return jsonResponse({
        Response: {
          ScoreSet: [
            completeScore({
              Exclusion: JSON.stringify({ Items: [{ Msg: `raw ${SECRET_KEY}` }] }),
            }),
          ],
          RequestId: 'rum-request-score-001',
        },
      });
    },
  });

  const result = await client.describeScoresV2({
    startTime: '2026082300',
    endTime: '2026083000',
    projectIds: [120000],
    type: 'day',
    env: 'gray',
  });

  assert.equal(calls.length, 1);
  const [url, request] = calls[0];
  assert.equal(url, TENCENT_RUM_ENDPOINT);
  assert.equal(request.method, 'POST');
  assert.deepEqual(JSON.parse(request.body), {
    StartTime: '2026082300',
    EndTime: '2026083000',
    IDList: [120000],
    Type: 'day',
    Env: 'gray',
  });
  assert.equal(request.headers.Host, TENCENT_RUM_HOST);
  assert.equal(request.headers['Content-Type'], 'application/json; charset=utf-8');
  assert.equal(request.headers['X-TC-Action'], TENCENT_RUM_ACTIONS.scores);
  assert.equal(request.headers['X-TC-Version'], TENCENT_RUM_VERSION);
  assert.equal(request.headers['X-TC-Region'], 'ap-guangzhou');
  assert.equal(request.headers['X-TC-Timestamp'], String(FIXED_TIMESTAMP));
  assert.equal(request.headers['X-TC-Token'], SESSION_TOKEN);
  assert.equal(
    request.headers.Authorization,
    independentAuthorization({ body: request.body, action: request.headers['X-TC-Action'] })
  );
  assert.doesNotMatch(request.headers.Authorization, new RegExp(SECRET_KEY));
  assert.doesNotMatch(request.headers.Authorization, new RegExp(SESSION_TOKEN));
  assert.equal(result.ScoreSet[0].PagePv, 42);
  assert.equal(result.ScoreSet[0].PageINP, 85.5);
  assert.equal('Exclusion' in result.ScoreSet[0], false);
  assert.doesNotMatch(JSON.stringify(result), new RegExp(SECRET_KEY));
  assert.equal(result.RequestId, 'rum-request-score-001');
});

test('typed PV, UV and project-limit methods validate and preserve the documented request casing', async () => {
  const calls = [];
  const client = createTencentRumClient({
    secretId: SECRET_ID,
    secretKey: SECRET_KEY,
    region: 'ap-guangzhou',
    now: () => FIXED_NOW.getTime(),
    fetch: async (_url, request) => {
      calls.push({ action: request.headers['X-TC-Action'], body: JSON.parse(request.body), headers: request.headers });
      if (request.headers['X-TC-Action'] === TENCENT_RUM_ACTIONS.pv) {
        return jsonResponse({
          Response: {
            ProjectPvSet: [{ ProjectId: 120000, Pv: '42', CreateTime: '20260830' }],
            RequestId: 'rum-pv-001',
          },
        });
      }
      if (request.headers['X-TC-Action'] === TENCENT_RUM_ACTIONS.uv) {
        return jsonResponse({
          Response: {
            ProjectUvSet: [{ ProjectId: 120000, Uv: '17', CreateTime: '20260830' }],
            RequestId: 'rum-uv-001',
          },
        });
      }
      return jsonResponse({
        Response: {
          ProjectLimitSet: [
            { ID: 17, ProjectID: 120000, ProjectInterface: 'web', ReportRate: 25, ReportType: 1 },
          ],
          RequestId: 'rum-limit-001',
        },
      });
    },
  });

  const pv = await client.describePvList({
    projectId: 120000,
    startTime: '20260823',
    endTime: '20260830',
    dimension: 'd',
  });
  const uv = await client.describeUvList({
    projectId: 120000,
    startTime: '20260830',
    endTime: '20260830',
    dimension: 'm',
  });
  const limits = await client.describeProjectLimits({ projectId: 120000 });

  assert.deepEqual(calls.map((call) => call.action), [
    TENCENT_RUM_ACTIONS.pv,
    TENCENT_RUM_ACTIONS.uv,
    TENCENT_RUM_ACTIONS.projectLimits,
  ]);
  assert.deepEqual(calls[0].body, {
    ProjectId: 120000,
    EndTime: '20260830',
    StartTime: '20260823',
    Dimension: 'd',
  });
  assert.deepEqual(calls[2].body, { ProjectID: 120000 });
  assert.equal('X-TC-Token' in calls[0].headers, false);
  assert.equal(pv.ProjectPvSet[0].Pv, '42');
  assert.equal(uv.ProjectUvSet[0].Uv, '17');
  assert.equal(limits.ProjectLimitSet[0].ReportRate, 25);
});

test('DescribeDataPvUrlStatisticsV2 parses columns dynamically and never returns the raw Result string', async () => {
  let captured;
  const rawResult = JSON.stringify({
    request_id: 'inner-request-001',
    results: [
      {
        statement_id: 0,
        total: 2,
        series: [
          {
            name: 'page_statistics',
            columns: ['url', 'pv', 'uv'],
            values: [['/about', 9, 5], ['/events', 4, 3]],
          },
        ],
      },
      { statement_id: 1, total: 13, offset: '' },
    ],
  });
  const client = createTencentRumClient({
    secretId: SECRET_ID,
    secretKey: SECRET_KEY,
    region: 'ap-guangzhou',
    clock: () => FIXED_NOW,
    fetchImpl: async (_url, request) => {
      captured = request;
      return jsonResponse({ Response: { Result: rawResult, RequestId: 'outer-request-001' } });
    },
  });

  const response = await client.describeDataPvUrlStatisticsV2({
    projectId: 120000,
    startTime: 1_787_385_600,
    endTime: 1_787_990_400,
    type: 'url',
    env: 'production',
    groupByType: 5,
    granularity: '1d',
  });

  assert.deepEqual(JSON.parse(captured.body), {
    StartTime: 1_787_385_600,
    Type: 'url',
    EndTime: 1_787_990_400,
    ID: 120000,
    IsNewData: 1,
    Env: 'production',
    GroupByType: 5,
    Granularity: '1d',
  });
  assert.equal(captured.headers['X-TC-Action'], TENCENT_RUM_ACTIONS.pageStatistics);
  assert.deepEqual(response.Result.results[0].series[0].rows, [
    { url: '/about', pv: 9, uv: 5 },
    { url: '/events', pv: 4, uv: 3 },
  ]);
  assert.equal(response.Result.results[1].total, 13);
  assert.equal(response.Result.requestId, 'inner-request-001');
  assert.equal(typeof response.Result, 'object');
  assert.doesNotMatch(JSON.stringify(response), /statement_id|values/);
});

test('dynamic V2 parser rejects malformed, oversized and prototype-sensitive series without echoing values', () => {
  const sensitive = `PRIVATE-${SECRET_KEY}`;
  for (const value of [
    `{ "results": [{ "series": [{ "columns": ["url"], "values": [["${sensitive}", 2]] }] }] }`,
    JSON.stringify({ results: [{ series: [{ columns: ['__proto__'], values: [[sensitive]] }] }] }),
    JSON.stringify({ results: [{ series: [{ columns: ['url'], values: [[{ secret: sensitive }]] }] }] }),
  ]) {
    assert.throws(
      () => parseTencentRumV2Result(value),
      (error) => {
        assert.ok(error instanceof TencentRumError);
        assert.equal(error.code, 'invalid_v2_result');
        assert.doesNotMatch(`${String(error)} ${JSON.stringify(error)} ${error.stack}`, new RegExp(SECRET_KEY));
        return true;
      }
    );
  }
});

test('API and transport errors are structured and never expose provider text, bodies or credentials', async () => {
  const providerMessage = `rejected ${SECRET_KEY} ${SESSION_TOKEN}`;
  const apiClient = createTencentRumClient({
    secretId: SECRET_ID,
    secretKey: SECRET_KEY,
    sessionToken: SESSION_TOKEN,
    region: 'ap-guangzhou',
    clock: () => FIXED_NOW,
    fetchImpl: async () =>
      jsonResponse(
        {
          Response: {
            Error: { Code: 'AuthFailure.SignatureFailure', Message: providerMessage },
            RequestId: 'rum-auth-failure-001',
          },
        },
        401
      ),
  });
  await assert.rejects(
    apiClient.describeProjectLimits({ projectId: 120000 }),
    (error) => {
      assert.ok(error instanceof TencentRumError);
      assert.equal(error.code, 'api_error');
      assert.equal(error.providerCode, 'AuthFailure.SignatureFailure');
      assert.equal(error.requestId, 'rum-auth-failure-001');
      assert.equal(error.statusCode, 401);
      const exposed = `${String(error)}\n${JSON.stringify(error)}\n${error.stack}`;
      assert.doesNotMatch(exposed, new RegExp(SECRET_KEY));
      assert.doesNotMatch(exposed, new RegExp(SESSION_TOKEN));
      assert.doesNotMatch(exposed, /rejected/);
      return true;
    }
  );

  const transportClient = createTencentRumClient({
    secretId: SECRET_ID,
    secretKey: SECRET_KEY,
    sessionToken: SESSION_TOKEN,
    region: 'ap-guangzhou',
    clock: () => FIXED_NOW,
    fetchImpl: async () => {
      throw new Error(`socket ${SECRET_KEY}`);
    },
  });
  await assert.rejects(
    transportClient.describeProjectLimits({ projectId: 120000 }),
    (error) => {
      assert.ok(error instanceof TencentRumError);
      assert.equal(error.code, 'transport_error');
      assert.doesNotMatch(`${String(error)} ${JSON.stringify(error)} ${error.stack}`, new RegExp(SECRET_KEY));
      assert.doesNotMatch(String(error), /socket/);
      return true;
    }
  );
});

test('configuration and method request validation is strict before network access', async () => {
  const leakedSecret = 'secret-that-must-not-appear';
  assert.throws(
    () =>
      createTencentRumClient({
        secretId: SECRET_ID,
        secretKey: leakedSecret,
        region: 'ap-guangzhou',
        [leakedSecret]: true,
      }),
    (error) => {
      assert.ok(error instanceof TencentRumError);
      assert.equal(error.code, 'invalid_config');
      assert.equal(error.field, 'unknown');
      assert.doesNotMatch(`${String(error)} ${JSON.stringify(error)}`, new RegExp(leakedSecret));
      return true;
    }
  );

  let calls = 0;
  const client = createTencentRumClient({
    secretId: SECRET_ID,
    secretKey: SECRET_KEY,
    region: 'ap-guangzhou',
    clock: () => FIXED_NOW,
    fetchImpl: async () => {
      calls += 1;
      throw new Error('network must not be reached');
    },
  });
  const invalidCalls = [
    () => client.describeScoresV2({ startTime: '2026023000', endTime: '2026030100' }),
    () => client.describeScoresV2({ startTime: '2026083000', endTime: '2026082300' }),
    () => client.describePvList({ projectId: 0, startTime: '20260823', endTime: '20260830' }),
    () => client.describeUvList({ projectId: 1, startTime: '20260823', endTime: '20260830', dimension: 'hour' }),
    () => client.describeProjectLimits({ projectId: 1, unexpected: SECRET_KEY }),
    () =>
      client.describeDataPvUrlStatisticsV2({
        projectId: 1,
        startTime: 200,
        endTime: 100,
        type: 'url',
      }),
    () =>
      client.describeRumLogList({
        projectId: 1,
        startTime: 200,
        endTime: 100,
        orderBy: 'asc',
        limit: 100,
        page: 1,
        query: 'id:1 AND type:pv',
      }),
  ];
  for (const invoke of invalidCalls) {
    await assert.rejects(invoke, (error) => error instanceof TencentRumError && error.code === 'invalid_request');
  }
  assert.equal(calls, 0);
});

test('DescribeRumLogList returns only privacy-safe page journey fields', async () => {
  const calls = [];
  const privateValues = {
    aid: 'aid-private-00000001',
    sessionId: 'session-private-00000001',
    ip: '203.0.113.25',
    userAgent: 'private-user-agent',
    uin: 'private-user-account',
    query: 'private-search-query',
  };
  const client = createTencentRumClient({
    secretId: SECRET_ID,
    secretKey: SECRET_KEY,
    region: 'ap-guangzhou',
    clock: () => FIXED_NOW,
    fetchImpl: async (...args) => {
      calls.push(args);
      return jsonResponse({
        Response: {
          RequestId: 'rum-log-list-001',
          Result: JSON.stringify({
            code: 0,
            data: {
              total: 2,
              limit: 100,
              page: 1,
              list: [
                {
                  date: FIXED_NOW.getTime(),
                  aid: privateValues.aid,
                  sessionId: privateValues.sessionId,
                  type: 'pv',
                  url: 'example.invalid/products-services?token=private',
                  from: 'https://example.invalid/',
                  referer: `https://www.baidu.com/s?wd=${privateValues.query}`,
                  ip: privateValues.ip,
                  userAgent: privateValues.userAgent,
                  uin: privateValues.uin,
                  msg: `raw ${SECRET_KEY}`,
                },
                { date: FIXED_NOW.getTime(), aid: '', sessionId: '', url: '/invalid' },
              ],
            },
          }),
        },
      });
    },
  });

  const result = await client.describeRumLogList({
    projectId: 159230,
    startTime: FIXED_NOW.getTime() - 60_000,
    endTime: FIXED_NOW.getTime(),
    orderBy: 'asc',
    limit: 100,
    page: 1,
    query: 'id:159230 AND type:pv AND env:production',
  });

  assert.equal(calls.length, 1);
  const [, request] = calls[0];
  assert.equal(request.headers['X-TC-Action'], TENCENT_RUM_ACTIONS.logs);
  assert.deepEqual(JSON.parse(request.body), {
    OrderBy: 'asc',
    StartTime: String(FIXED_NOW.getTime() - 60_000),
    Limit: 100,
    Page: 1,
    Query: 'id:159230 AND type:pv AND env:production',
    EndTime: String(FIXED_NOW.getTime()),
    ID: 159230,
  });
  assert.equal(result.total, 2);
  assert.equal(result.invalidRows, 1);
  assert.equal(result.records.length, 1);
  assert.deepEqual(
    Object.keys(result.records[0]).sort(),
    ['activeMs', 'ctaId', 'eventName', 'logType', 'path', 'placement', 'previousPath', 'scrollBucket', 'sessionHash', 'source', 'timestamp', 'visitorHash']
  );
  assert.equal(result.records[0].path, '/products-services');
  assert.equal(result.records[0].previousPath, '/');
  assert.equal(result.records[0].source, 'organic');
  assert.match(result.records[0].sessionHash, /^[a-f0-9]{64}$/);
  assert.match(result.records[0].visitorHash, /^[a-f0-9]{64}$/);
  const exposed = JSON.stringify(result);
  for (const value of Object.values(privateValues)) assert.doesNotMatch(exposed, new RegExp(value));
  assert.doesNotMatch(exposed, /token=|msg|referer|userAgent|uin|ip/);
});

test('DescribeRumLogList projects official engagement fields and prefers the sanitized ext3 path', async () => {
  const client = createTencentRumClient({
    secretId: SECRET_ID,
    secretKey: SECRET_KEY,
    region: 'ap-guangzhou',
    clock: () => FIXED_NOW,
    fetchImpl: async () => jsonResponse({
      Response: {
        RequestId: 'rum-log-engagement-001',
        Result: JSON.stringify({
          code: 0,
          data: {
            total: 3,
            limit: 100,
            page: 1,
            list: [
              {
                date: FIXED_NOW.getTime(),
                aid: 'aid-engagement-0001',
                sessionId: 'session-engagement-0001',
                type: 'event',
                name: 'engagement',
                msg: 'wrong_event_name',
                ext1: '12345',
                ext2: '75',
                ext3: '/products-services/knowledge-base?token=private',
                url: '/wrong-runtime-route',
                originFrom: 'https://example.invalid/wrong-runtime-route',
              },
              {
                date: FIXED_NOW.getTime(),
                aid: 'aid-invalid-type-0001',
                sessionId: 'session-invalid-type-0001',
                type: 'unknown',
                url: '/',
              },
              {
                date: FIXED_NOW.getTime(),
                aid: 'aid-invalid-scroll-0001',
                sessionId: 'session-invalid-scroll-0001',
                type: 'event',
                name: 'engagement',
                ext1: '1000',
                ext2: '33',
                ext3: '/',
              },
            ],
          },
        }),
      },
    }),
  });

  const result = await client.describeRumLogList({
    projectId: 159230,
    startTime: FIXED_NOW.getTime() - 60_000,
    endTime: FIXED_NOW.getTime(),
    orderBy: 'asc',
    limit: 100,
    page: 1,
    query: 'id:159230 AND type:event AND env:production',
  });

  assert.equal(result.records.length, 1);
  assert.equal(result.invalidRows, 2);
  assert.equal(result.records[0].path, '/products-services/knowledge-base');
  assert.equal(result.records[0].eventName, 'engagement');
  assert.equal(result.records[0].activeMs, 12345);
  assert.equal(result.records[0].scrollBucket, 75);
  assert.doesNotMatch(JSON.stringify(result), /token=private|wrong-runtime-route/);
});

test('DescribeRumLogList accepts an omitted empty list and preserves public event slugs', async () => {
  let call = 0;
  const client = createTencentRumClient({
    secretId: SECRET_ID,
    secretKey: SECRET_KEY,
    region: 'ap-guangzhou',
    clock: () => FIXED_NOW,
    fetchImpl: async () => {
      call += 1;
      const data = call === 1
        ? { total: 0, limit: 100, page: 1 }
        : {
            total: 1,
            limit: 100,
            page: 1,
            list: [{
              date: FIXED_NOW.getTime(),
              aid: 'aid-public-event-0001',
              sessionId: 'session-public-event-0001',
              url: 'example.invalid/events/evt_052636653cc0102afd1e',
              from: 'https://example.invalid/events',
              referer: '',
              type: 'pv',
            }],
          };
      return jsonResponse({
        Response: { Result: JSON.stringify({ code: 0, data }), RequestId: `rum-log-empty-${call}` },
      });
    },
  });
  const input = {
    projectId: 159230,
    startTime: FIXED_NOW.getTime() - 60_000,
    endTime: FIXED_NOW.getTime(),
    orderBy: 'asc',
    limit: 100,
    page: 1,
    query: 'id:159230 AND type:pv',
  };
  const empty = await client.describeRumLogList(input);
  assert.deepEqual(empty.records, []);
  const event = await client.describeRumLogList(input);
  assert.equal(event.records[0].path, '/events/evt_052636653cc0102afd1e');
});

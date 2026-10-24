'use strict';

const fs = require('node:fs');
const path = require('node:path');
const {execFile} = require('node:child_process');
const {pathToFileURL} = require('node:url');

const SNAPSHOT_TTL = 30_000;
const STALE_AFTER = 90_000;
const COLLECTOR_TIMEOUT = 25_000;
const STATES = new Set(['healthy', 'attention', 'unknown', 'waiting']);

function evidenceKey(item) {
  const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === 'object'
    ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
  return JSON.stringify(canonical({status: item.status, codes: (item.codes || []).slice().sort(),
    queues: [...(item.queues || []), ...(item.receipts || [])].map(queue => ({source: queue.source, table: queue.table, state: queue.state, counts: queue.counts})),
    runtimeCode: item.runtimeCode || []}));
}

function validSnapshot(value, now) {
  if (value?.schemaVersion !== 1 || !Array.isArray(value.items) || value.items.length > 1000) return false;
  const observed = Date.parse(value.observedAt);
  if (!Number.isFinite(observed) || observed > now + 5000) return false;
  const ids = new Set();
  return value.items.every(item => {
    if (!item || typeof item.id !== 'string' || item.id.length > 200 || ids.has(item.id) || !STATES.has(item.status)) return false;
    ids.add(item.id);
    return true;
  });
}

class WorkflowService {
  constructor({stateRoot, studioStateRoot = stateRoot, run, clock = () => Date.now()} = {}) {
    this.root = path.join(stateRoot, 'workflow-monitor');
    this.stateRoot = stateRoot;
    this.studioStateRoot = studioStateRoot;
    this.clock = clock;
    this.run = run || (() => this.collect());
    this.value = null;
    this.flight = null;
    this.history = [];
    this.disposed = false;
    try {
      const file = path.join(this.root, 'snapshot.json');
      if (fs.statSync(file).size > 4 * 1024 * 1024) throw Error('cache_size_exceeded');
      const cached = JSON.parse(fs.readFileSync(file, 'utf8'));
      if (cached.schemaVersion === 1 && validSnapshot(cached.snapshot, this.clock())) {
        this.value = cached.snapshot;
        this.history = Array.isArray(cached.history) ? cached.history.filter(event =>
          typeof event.id === 'string' && STATES.has(event.from) && STATES.has(event.to) && Number.isFinite(Date.parse(event.at))
        ).slice(-200) : [];
      }
    } catch {
      // Keep damaged technical cache on disk; other App modules remain unaffected.
    }
  }

  collect() {
    return new Promise((resolve, reject) => {
      const child = execFile(process.env.CONTENTBASE_PYTHON||'python3', [path.join(__dirname, 'collector.py')], {
        timeout: COLLECTOR_TIMEOUT,
        maxBuffer: 3 * 1024 * 1024,
        env: {HOME: process.env.HOME, PATH: process.env.PATH, CONTENTBASE_RUNTIME_ROOT: process.env.CONTENTBASE_RUNTIME_ROOT||path.join(this.stateRoot,'workflows'), PYTHONDONTWRITEBYTECODE: '1'},
      }, (error, stdout) => {
        if (this.child === child) this.child = null;
        if (error) return reject(Error('workflow_collector_failed'));
        try {
          const result = JSON.parse(stdout);
          if (!result.ok || !validSnapshot(result.data, this.clock())) throw Error('protocol');
          resolve(result.data);
        } catch {
          reject(Error('workflow_collector_protocol'));
        }
      });
      this.child = child;
      // Native App chooses this root, never the renderer request.
      child.stdin.end(JSON.stringify({operation: 'snapshot', studioStateRoot: this.studioStateRoot}));
    });
  }

  async snapshot(force = false) {
    if (this.disposed) throw Error('workflow_disposed');
    if (this.flight) return this.flight;
    if (!force && this.value && this.clock() - Date.parse(this.value.observedAt) < SNAPSHOT_TTL) return this.envelope('cached');
    this.flight = this.run().then(value => {
      if (this.disposed) throw Error('workflow_disposed');
      if (!validSnapshot(value, this.clock())) throw Error('workflow_collector_protocol');
      const old = new Map((this.value?.items || []).map(item => [item.id, item]));
      for (const item of value.items) {
        const previous = old.get(item.id);
        if (previous && evidenceKey(previous) !== evidenceKey(item)) {
          this.history.push({id: item.id, kind: previous.status !== item.status ? 'status' : 'evidence',
            from: previous.status, to: item.status, codes: item.codes || [], at: value.observedAt});
        }
      }
      this.history = this.history.slice(-200);
      this.value = value;
      this.persist();
      return this.envelope('live');
    }).catch(() => {
      if (this.disposed) throw Error('workflow_disposed');
      return {ok: false, phase: this.value ? 'stale' : 'error', error: 'workflow_source_unavailable',
        ...(this.value ? {data: this.value, history: this.history} : {}), readOnly: true};
    }).finally(() => { this.flight = null; });
    return this.flight;
  }

  envelope(phase) {
    const age = this.clock() - Date.parse(this.value.observedAt);
    return {ok: true, phase: age > STALE_AFTER ? 'stale' : phase, data: this.value, history: this.history, readOnly: true};
  }

  persist() {
    fs.mkdirSync(this.root, {recursive: true, mode: 0o700});
    const file = path.join(this.root, 'snapshot.json');
    fs.writeFileSync(file + '.tmp', JSON.stringify({schemaVersion: 1, snapshot: this.value, history: this.history}), {mode: 0o600});
    fs.renameSync(file + '.tmp', file);
    fs.chmodSync(file, 0o600);
  }

  dispose() { this.disposed = true; this.child?.kill(); }

  async dispatch(input) {
    if (!input || Array.isArray(input) || typeof input !== 'object' || Object.keys(input).some(key => !['operation', 'id'].includes(key))) throw Error('workflow_input_invalid');
    if (input.operation === 'snapshot' && !input.id) return this.snapshot(false);
    if (input.operation === 'refresh' && !input.id) return this.snapshot(true);
    if (input.operation === 'detail' && typeof input.id === 'string' && input.id.length < 200) {
      const response = await this.snapshot();
      const item = response.data?.items.find(row => row.id === input.id);
      if (!item) throw Error('workflow_id_invalid');
      return {...response, data: {item, observedAt: response.data.observedAt}, history: this.history.filter(event => event.id === input.id)};
    }
    throw Error('workflow_operation_denied');
  }
}

function registerWorkflow({win, ipcMain, stateRoot, studioStateRoot = stateRoot, uiFile}) {
  const service = new WorkflowService({stateRoot, studioStateRoot});
  const url = pathToFileURL(uiFile).href;
  ipcMain.handle('studio:workflow', async (event, input) => {
    if (event.sender !== win.webContents || event.senderFrame !== win.webContents.mainFrame || event.senderFrame.url.split('#')[0] !== url) throw Error('workflow_sender_denied');
    return service.dispatch(input);
  });
  win.on('closed', () => service.dispose());
  return service;
}

module.exports = {WorkflowService, registerWorkflow, validSnapshot, evidenceKey};

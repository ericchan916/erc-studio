'use strict';
// Routing registry stays authoritative; this command checks only monitor metadata.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const file = path.resolve(__dirname, '../src/workflow/catalog.json');
const registry = path.resolve(__dirname, '../../agent-runtime/workflow-registry.json');
const STATES = new Set(['state', 'count', 'studio-json', 'bar-json']);
const WHERE_COLUMNS = new Set(['source', 'action_id', 'workflow_id', 'active_workflow_id', 'kind']);

function relative(value) {
  return typeof value === 'string' && value.length < 400 && !path.isAbsolute(value) && !value.split(/[\\/]/).includes('..');
}

function audit(catalog, source) {
  const issues = [];
  if (catalog?.schemaVersion !== 1 || !Array.isArray(catalog.components) || !Array.isArray(catalog.routes) || !Array.isArray(source?.routes)) return ['catalog_shape_invalid'];
  const ids = new Set(['notifications']);
  const labels = new Set();
  for (const component of catalog.components) {
    if (typeof component.id !== 'string' || ids.has(component.id)) issues.push('duplicate_or_invalid_id:' + component.id);
    ids.add(component.id);
    if (component.label) {
      if (labels.has(component.label)) issues.push('duplicate_label:' + component.label);
      labels.add(component.label);
    }
    if (component.healthPath && !relative(component.healthPath)) issues.push('invalid_source:' + component.id);
    if (component.healthPath && (!Number.isFinite(component.maxAge) || component.maxAge <= 0 || !Array.isArray(component.statuses))) issues.push('health_contract_invalid:' + component.id);
  }
  for (const item of [...catalog.components, ...catalog.routes]) {
    if (!Array.isArray(item.dependencies) || item.dependencies.some(id => !ids.has(id))) issues.push('unknown_dependency:' + item.id);
    for (const receipt of item.receipts || []) {
      if (!STATES.has(receipt.mode || 'state') || !relative(receipt.path || receipt.file) || Object.keys(receipt.where || {}).some(key => !WHERE_COLUMNS.has(key))) issues.push('receipt_contract_invalid:' + item.id);
    }
  }
  const routeIds = new Set();
  for (const entry of catalog.routes) {
    if (routeIds.has(entry.routeId)) issues.push('duplicate_route:' + entry.routeId);
    routeIds.add(entry.routeId);
    if (!Array.isArray(entry.receipts)) issues.push('receipt_binding_missing:' + entry.routeId);
  }
  for (const route of source.routes) {
    const entry = catalog.routes.find(item => item.routeId === route.id);
    if (!entry) issues.push('route_unregistered:' + route.id);
    else for (const key of ['name', 'trigger', 'action', 'boundary']) {
      if (entry[key] !== route[key]) issues.push('route_metadata_drift:' + route.id + ':' + key);
    }
  }
  for (const entry of catalog.routes) if (!source.routes.some(route => route.id === entry.routeId)) issues.push('route_removed:' + entry.routeId);
  return issues;
}

function sync(catalog, source, bytes) {
  // New routes require an explicit dependency/receipt design, never a silent default.
  catalog.routes = source.routes.map(route => {
    const old = catalog.routes.find(entry => entry.routeId === route.id);
    if (!old) throw Error('new_route_requires_dependency_mapping:' + route.id);
    return {...old, ...Object.fromEntries(['name', 'trigger', 'action', 'boundary'].map(key => [key, route[key]]))};
  });
  catalog.registryVersion = source.registry_version;
  catalog.registrySha256 = crypto.createHash('sha256').update(bytes).digest('hex');
  return catalog;
}

if (require.main === module) {
  const catalog = JSON.parse(fs.readFileSync(file, 'utf8'));
  const raw = fs.readFileSync(registry);
  const source = JSON.parse(raw);
  if (process.argv.includes('--sync')) {
    sync(catalog, source, raw);
    const issues = audit(catalog, source);
    if (issues.length) throw Error(issues.join(','));
    fs.writeFileSync(file, JSON.stringify(catalog, null, 2) + '\n');
  }
  const issues = audit(catalog, source);
  console.log(JSON.stringify({ok: !issues.length, services: catalog.components.length + 1, routes: catalog.routes.length, issues}));
  process.exitCode = issues.length ? 1 : 0;
}
module.exports = {audit, sync};

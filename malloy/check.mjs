// Run malloy/aao/aao.malloy on DuckDB and diff its KPIs against checks/golden/ (PLAN.md step 12).
// The marts come from `uv run checks/local_dataform.py --export-marts <dir>`; the DuckDB
// connection is named "bigquery" so the model runs unchanged.
//
// Usage: node check.mjs <marts dir>

import {readFileSync, readdirSync} from 'node:fs';
import {dirname, join} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {SingleConnectionRuntime} from '@malloydata/malloy';
import {DuckDBConnection} from '@malloydata/db-duckdb';

const here = dirname(fileURLToPath(import.meta.url));
const golden = join(here, '..', 'checks', 'golden');
const TOLERANCE = 0.01;

function readCsv(path) {
  // Golden files hold no quoted commas in the columns compared here.
  const [header, ...lines] = readFileSync(path, 'utf8').trim().split('\n');
  const cols = header.split(',');
  return lines.map(line => Object.fromEntries(line.split(',').map((v, i) => [cols[i], v])));
}

function close(expected, actual) {
  // Golden NULL = "does not apply". Malloy's sum() of only NULLs is 0, which is fine for a measure.
  if (expected === '') return actual === null || actual === undefined || Number(actual) === 0;
  return Math.abs(Number(expected) - Number(actual)) <= TOLERANCE;
}

const martsDir = process.argv[2];
if (!martsDir) {
  console.error('usage: node check.mjs <marts dir>');
  process.exit(2);
}

const connection = new DuckDBConnection('bigquery');
await connection.runSQL('create schema if not exists analytics');
for (const file of readdirSync(martsDir).filter(f => f.endsWith('.parquet'))) {
  const table = file.replace('.parquet', '');
  await connection.runSQL(
    `create or replace view analytics.${table} as select * from read_parquet('${join(martsDir, file)}')`
  );
}

const runtime = new SingleConnectionRuntime({
  urlReader: {readURL: async url => readFileSync(fileURLToPath(url), 'utf8')},
  connection,
});
const model = runtime.loadModel(pathToFileURL(join(here, 'aao', 'aao.malloy')));
const run = async query => (await model.loadQuery(query).run({rowLimit: 1_000_000})).data.toObject();

const errors = [];
const check = (label, ok, detail) => {
  console.log(`  ${ok ? 'ok ' : 'BAD'} ${label}`);
  if (!ok) errors.push(`${label}: ${detail}`);
};

// T4: KPI tiles.
const [kpis] = await run('run: markets -> kpi_totals');
const [goldenKpis] = readCsv(join(golden, 'kpi_totals.csv'));
for (const kpi of ['settled_earnings', 'settled_stake', 'settled_margin', 'open_bets', 'worst_case_future', 'best_case_future']) {
  check(`kpi_totals.${kpi} = ${Number(kpis[kpi]).toFixed(2)}`, close(goldenKpis[kpi], kpis[kpi]), `golden ${goldenKpis[kpi]}`);
}

// T1: per fixture, through the joins (one 3-way market per fixture in the golden file).
const perFixture = await run(`
  run: markets -> {
    where: market_type.market_name = '3-way'
    group_by: market_id
    aggregate: settled_earnings, open_bets, worst_case_future, best_case_future
  }`);
const byMarket = new Map(perFixture.map(r => [String(r.market_id), r]));
const summary = readCsv(join(golden, 'market_summary.csv'));
const bad = summary.filter(g => {
  const r = byMarket.get(g.market_id);
  return !r || !close(g.settled_earnings, r.settled_earnings) || !close(g.open_bets, r.open_bets) ||
    !close(g.worst_case, r.worst_case_future) || !close(g.best_case, r.best_case_future);
});
check(`per market: ${summary.length - bad.length}/${summary.length} match golden/market_summary.csv`, bad.length === 0,
  `first mismatch market ${bad[0]?.market_id}`);

// Views used by the dashboard must at least run.
for (const view of ['by_fixture_date', 'by_fixture', 'by_market_type']) {
  const rows = await run(`run: markets -> ${view}`);
  check(`view ${view} runs (${rows.length} rows)`, rows.length > 0, 'no rows');
}
const outcomeRows = await run('run: outcomes -> by_option');
check(`view outcomes.by_option runs (${outcomeRows.length} rows)`, outcomeRows.length > 0, 'no rows');

if (errors.length) {
  console.error('\nMALLOY CHECK FAILED:\n  ' + errors.join('\n  '));
  process.exit(1);
}
console.log('\nmalloy check ok');

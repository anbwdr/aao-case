// Small SQL snippets reused across models.

// Keep the latest landed version of each key across batches (raw is append-only).
function latestPerKey(key) {
  return `qualify row_number() over (partition by ${key} order by _ingested_at desc, _batch_id desc) = 1`;
}

// SQL list literal, e.g. inList(["1", "X"]) -> ('1', 'X').
function inList(values) {
  return `(${values.map((v) => `'${v}'`).join(", ")})`;
}

module.exports = { latestPerKey, inList };

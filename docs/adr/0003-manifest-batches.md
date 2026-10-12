# 3. Batches land with a manifest, raw is append-only

**Decision.** Each upload is a folder `batch=<utc ts>/` with its CSVs and a `_MANIFEST.json` written last (files, columns, row counts). Only the manifest starts the Workflow. Raw keeps every batch as STRING plus `_batch_id`, `_ingested_at`, `_source_file`; base takes the latest row per key.

**Consequences.**
- Partial uploads never run: no manifest, no run.
- Retries are safe: a file's rows are deleted by `_batch_id` before insert, and a batch already in `batch_manifest` is skipped.
- Raw row counts must equal the manifest (Workflow check and Dataform assertion).
- Markets are views over the latest raw row because `result_option_id` changes on settlement; bets are append-only, so `base_bets` is incremental on `_batch_id`, merged on `bet_id`.

# The invariant register

**Generated from `tests/invariants/` — do not edit by hand.**
Regenerate with `uv run python -m tests.invariants.register`.

Every guarantee this system makes is a test in `tests/invariants/`, not a
sentence in a docstring. A row is *enforced* when its test passes today, and
*pending* when the test exists but the behaviour does not yet — those are
marked `xfail(strict=True)`, so the build fails the moment one starts passing
and the marker has to come off. That is what keeps this document honest.

**31 enforced · 15 pending · 46 total**

## durable execution

- ✅ **The baseline: with no injected failure the card is charged exactly once and the run completes. If this breaks, every other row is meaningless.**
  `test_the_clean_run_is_correct`
- ⏳ **A journaled call must record its intent before executing, so a replay can tell 'never ran' from 'ran, outcome unknown'.**
  `test_i07_intent_is_journaled_before_execution`
  _Pending — I7: no effect.intent entry exists — an effect is journaled only after it runs, so the crash window is invisible. Fixed in step 3._
- ⏳ **However the runtime fails and recovers, a non-idempotent tool that has already run must never run again.**
  `test_i09_a_completed_effect_never_re_executes`
  _Pending — I9: a durable-write failure after the tool ran re-executes it — the card is charged twice. Fixed in step 3 (intent/outcome + idempotent)._
- ⏳ **Liveness half of the atomic-commit invariant: one failed durable write must not strand a run forever. A run that never terminates holds its thread's single-flight slot and never reports to its caller.**
  `test_i10_a_single_durable_failure_still_reaches_a_terminal_state`
  _Pending — I10: a failure while appending run.started or the first effect leaves the run with no terminal state at all. Fixed in step 3._
- ⏳ **A run log is the source of truth for history, billing and projection. Two terminal entries make all three wrong.**
  `test_i11_at_most_one_terminal_entry_per_run`
  _Pending — I11: the terminal transition spans journal, queue and inbox non-atomically, so recovery appends run.completed a second time. Fixed in step 3 (single commit)._
- ⏳ **A child's body is as side-effecting as a tool. Recovery must not re-run it, and the parent must still be woken exactly once.**
  `test_i09_spawned_children_run_their_body_once`
  _Pending — I9/I10 for the supervision path: a failure in the terminal window re-runs the child body and duplicates its terminal entry. Fixed in step 3._
- ✅ **Guards the harness itself: a matrix that silently stopped injecting would make every row above pass for the wrong reason.**
  `test_the_matrix_actually_injected_every_point`

## inbox

- ✅ **The half that works: while a message is in flight, redelivering it is a no-op rather than a duplicate.**
  `test_redelivery_before_ack_is_deduplicated`
- ⏳ **At-least-once transports redeliver after the consumer has committed — that is the normal case, not an edge case. The inbox is what absorbs it.**
  `test_i12_redelivery_after_ack_is_rejected`
  _Pending — I12: ack deletes the dedup record, so a transport redelivering an already-processed message id gets it accepted and the agent handles it twice. Fixed in step 3 (processed watermark in the commit)._

## journal

- ⏳ **Two replies differing only in length must journal the same number of rows: the durable record of a turn is the finished message.**
  `test_journal_size_does_not_grow_with_token_count`
  _Pending — Journal/stream split: one durable row is appended per streamed token, so the log grows with reply length and every fold() re-reads it. Fixed in step 3 (ephemeral stream channel + append_many)._

## model boundary

- ✅ **A window that starts mid-turn must not keep a tool result whose call it dropped.**
  `test_i18_compaction_never_leaves_an_orphaned_tool_result`
- ✅ **Truncation is about text length. An image is not text and must survive it — this is the regression that made the model blind to its own charts.**
  `test_i18_truncating_a_tool_result_keeps_its_media`
- ✅ **A client must never hand a provider content the model cannot accept — the provider either rejects the request or silently ignores the content.**
  `test_i16_no_content_outside_the_models_modalities_survives`
- ✅ **Dropping media silently makes the model answer as if it never existed. It has to be told something was there.**
  `test_i16_dropped_content_leaves_a_note`
- ✅ **Budgets are enforced against accumulated usage; the accumulation has to be exact.**
  `test_i19_accumulated_usage_equals_the_sum_of_its_parts`
- ✅ **Cost drives the budget that stops a runaway agent. A negative or non-monotonic cost disables it.**
  `test_i19_cost_is_never_negative_and_rises_with_usage`

## register is current

- ✅ **the register document matches the tests**
  `test_the_register_document_matches_the_tests`
- ✅ **Guards the collector against the gap it already had once.**
  `test_the_register_lists_every_invariant_test`

## replay determinism

- ✅ **The core property. Replay a program with an arbitrary set of calls already journaled; every call that still runs must land on the path it had live.**
  `test_i06_a_calls_path_is_the_same_on_replay_as_it_was_live`
- ✅ **Two distinct calls sharing a path share an effect id: one would be served the other's cached result.**
  `test_i06_no_two_calls_share_a_path`
- ✅ **Scope push/pop must balance. An unbalanced stack silently shifts every subsequent path in the run.**
  `test_i06_the_allocator_returns_to_its_starting_depth`

## structure

- ✅ **The kernel is the engine, so it has to be installable and importable without a vendor SDK, a model runtime, or a database driver.**
  `test_i26_the_kernel_imports_only_its_allowed_third_party_set`
- ✅ **``abstractions`` is what an adapter author depends on. If it reaches back into the engine, implementing a port drags the whole engine along.**
  `test_i27_abstractions_never_imports_the_engine`
- ✅ **Every addition or removal in the public API shows up as a diff in ``public_api.json``, so it is reviewed rather than noticed later.**
  `test_i28_the_public_api_matches_its_snapshot`
- ⏳ **The row that keeps the other rows honest.**
  `test_i30_every_port_has_a_conformance_suite_and_every_impl_runs_it`
  _Pending — I30: no port has a conformance suite yet — they land with their ports in steps 3-6._

## telemetry

- ⏳ **A trace that is four traces cannot answer 'what did this run do', which is the only question it exists to answer.**
  `test_i24_one_turn_is_one_trace`
  _Pending — I24: spans are started without being made current, so each one is its own root and a turn produces several disconnected traces. Fixed in step 2 (one telemetry module)._
- ⏳ **i24 only the run span is a root**
  `test_i24_only_the_run_span_is_a_root`
  _Pending — I24: only the agent-turn span has no parent; the llm and tool spans are roots too. Fixed in step 2._
- ⏳ **A span with no outcome recorded is a timing bar and nothing more.**
  `test_i24_spans_carry_their_outcome`
  _Pending — I24: outcome attributes are set after the span is ended, where OpenTelemetry discards them ('Setting attribute on ended span'). Fixed in step 2._
- ✅ **Telemetry leaves the erasure boundary: it is sampled, exported to third parties and retained on their schedule. Content goes in it only when a deployment explicitly opts in.**
  `test_i25_no_prompt_or_tool_content_appears_in_span_attributes`

## tenancy

- ⏳ **Ids come from request bodies and model output. An id alone must never be enough to address a record.**
  `test_i03_a_record_is_not_readable_from_another_tenant`
  _Pending — I3: MemoryStore.get/delete/touch take a bare id, so one tenant can read another's record by guessing or reusing an id. Fixed in step 5 (scope-bound handles)._
- ⏳ **``save`` is an upsert keyed by id, and the id is caller-supplied.**
  `test_i03_one_tenant_cannot_overwrite_another_tenants_record`
  _Pending — I3: an id supplied by a caller overwrites an existing record belonging to a different tenant. Fixed in step 5._
- ⏳ **The dangerous default: forgetting a field widens the query instead of narrowing it, and nothing in the type system notices.**
  `test_i03_omitting_a_scope_field_is_not_a_wildcard`
  _Pending — I3: MemoryNamespace treats user_id=None as a wildcard, so a query that merely omits the user reads every user in the tenant. Fixed in step 5 (no implicit wildcard; tenant_wide must be explicit)._
- ⏳ **A deletion request has to reach every store, including the journal that holds the raw conversation. Today the GDPR eraser touches neither memory nor the event log.**
  `test_i04_a_scope_can_be_erased_completely`
  _Pending — I4: no store exposes erase(scope) and the run journal is never erased at all, so a deletion request cannot be satisfied. Fixed in step 5._

## type safety

- ✅ **``UnknownBlock`` exists for *future provider* types, not for malformed input. Swallowing a missing discriminator turns a bug into lost content.**
  `test_a_block_without_a_type_is_rejected`
- ✅ **a misspelled known type is rejected**
  `test_a_misspelled_known_type_is_rejected`
- ✅ **Messages are passed across agents, cached and journaled. A mutable 'immutable' message means one consumer can corrupt another's copy.**
  `test_content_is_deeply_immutable_and_hashable`
- ✅ **``str(block)`` is used to build prompts. It must never raise.**
  `test_structured_blocks_render_non_json_values`
- ✅ **``check()`` runs at every cooperative yield point. A deadline that makes it raise ``TypeError`` disables cancellation everywhere at once.**
  `test_a_naive_deadline_is_rejected_at_construction`
- ✅ **Workers may run in another process. An exception that cannot be pickled is reported as a pickling error instead of the real failure.**
  `test_structured_exceptions_survive_a_process_boundary`
- ✅ **Totalling usage across turns is the single most common thing a caller does with it.**
  `test_usage_accumulates_with_sum`
- ✅ **Risk is compared to decide whether approval is required. String ordering silently inverts that decision.**
  `test_tool_risk_is_ordered_by_severity`
- ✅ **Supervision is persisted and read back by a possibly older or newer worker. An unknown field must not crash the read.**
  `test_supervision_survives_a_round_trip_with_an_unknown_field`
- ✅ **tagged results cannot contradict their tag**
  `test_tagged_results_cannot_contradict_their_tag`
- ✅ **A manifest path comes from tool output and names a file to materialise.**
  `test_workspace_paths_cannot_escape_the_workspace`
- ✅ **Effect identity is computed from tool arguments, which routinely contain bytes and timestamps.**
  `test_effect_identity_handles_any_json_encodable_argument`
- ✅ **Time-sortable ids are what make a log or inbox orderable without a separate sequence column.**
  `test_ids_are_time_sortable`

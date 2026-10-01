# The invariant register

**Generated from `tests/invariants/` — do not edit by hand.**
Regenerate with `uv run python -m tests.invariants.register`.

Every guarantee this system makes is a test in `tests/invariants/`, not a
sentence in a docstring. A row is *enforced* when its test passes today, and
*pending* when the test exists but the behaviour does not yet — those are
marked `xfail(strict=True)`, so the build fails the moment one starts passing
and the marker has to come off. That is what keeps this document honest.

**84 enforced · 1 pending · 85 total**

## approvals

- ✅ **i23 an approval is journaled with who when and why**
  `test_i23_an_approval_is_journaled_with_who_when_and_why`
- ✅ **i23 a denial is journaled too**
  `test_i23_a_denial_is_journaled_too`
- ✅ **the result carries the attribution it was given**
  `test_the_result_carries_the_attribution_it_was_given`
- ✅ **a disconnect or timeout is a denial**
  `test_a_disconnect_or_timeout_is_a_denial`
- ✅ **The route stamps ``decided_by`` / ``decided_at`` from the authenticated caller. A client that puts someone else's name in its body is not believed.**
  `test_i23_the_server_names_the_approver_not_the_client`

## budgets

- ✅ **i20 total spend never exceeds the cap by more than the calls in flight**
  `test_i20_total_spend_never_exceeds_the_cap_by_more_than_the_calls_in_flight`
- ✅ **The same budget, split across many children, stops the tree as one agent would.**
  `test_i20_spawning_more_agents_is_not_a_way_around_a_cap`

## durable execution

- ✅ **The baseline: with no failure the card is charged once and the run completes.**
  `test_the_clean_run_is_correct`
- ✅ **A journaled call records its intent before it runs, so a replay can tell 'never started' from 'started, outcome unknown'.**
  `test_i07_intent_is_journaled_before_execution`
- ✅ **However the runtime fails and recovers, a tool that is not safe to run twice never runs twice.**
  `test_i09_a_completed_effect_never_re_executes`
- ✅ **One failed write must not strand a run. A run that never ends holds its thread's single-flight slot forever and never reports to its caller.**
  `test_i10_a_single_durable_failure_still_reaches_a_terminal_state`
- ✅ **A run's record is the source of truth for history, billing and projection. Two terminal entries make all three wrong.**
  `test_i11_at_most_one_terminal_entry_per_run`
- ✅ **A dropped connection, not a crash: the work was done, only a record failed, so the run completes — retrying the record rather than failing the run.**
  `test_a_failure_the_worker_can_handle_never_loses_the_run`
- ✅ **If the worker dies after starting a tool that is not safe to run twice, nothing can say whether it took effect. The run fails rather than guess; the journaled intent is what a person compensates from.**
  `test_i08_a_killed_worker_leaves_an_effect_in_doubt_and_the_run_says_so`
- ✅ **A child's side effects are as protected as a parent's, and the parent is woken exactly once.**
  `test_i09_spawned_children_run_their_journaled_effects_once`
- ✅ **i11 a parent run ends exactly once even when its child does**
  `test_i11_a_parent_run_ends_exactly_once_even_when_its_child_does`
- ✅ **i10 a parent waiting on a child is never stranded**
  `test_i10_a_parent_waiting_on_a_child_is_never_stranded`
- ✅ **Guards the harness itself: a matrix that silently stopped injecting would make every row above pass for the wrong reason.**
  `test_the_matrix_actually_injected_every_point`

## inbox

- ✅ **While a message is in flight, redelivering it is a no-op rather than a duplicate.**
  `test_redelivery_before_ack_is_deduplicated`
- ✅ **The consumer committed (acked) the message; a later redelivery of the same id must not reach the agent again.**
  `test_i12_redelivery_after_ack_is_rejected`

## journal

- ✅ **Two replies differing only in length must journal the same number of rows: the durable record of a turn is the finished message.**
  `test_journal_size_does_not_grow_with_token_count`

## liveness

- ✅ **i13 a run longer than its lease completes once**
  `test_i13_a_run_longer_than_its_lease_completes_once`

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

## provider outcomes

- ✅ **a rate limit carries the providers retry after**
  `test_a_rate_limit_carries_the_providers_retry_after`
- ✅ **retry after in milliseconds is understood**
  `test_retry_after_in_milliseconds_is_understood`
- ✅ **a rate limit without a hint still types as rate limited**
  `test_a_rate_limit_without_a_hint_still_types_as_rate_limited`
- ✅ **a context overflow is distinguishable from any other bad request**
  `test_a_context_overflow_is_distinguishable_from_any_other_bad_request`
- ✅ **Clients wrap the SDK's exception in their own; the status and headers live on the cause.**
  `test_a_wrapped_sdk_error_is_still_classified`
- ✅ **credentials content filter and other client errors are typed**
  `test_credentials_content_filter_and_other_client_errors_are_typed`
- ✅ **a server error stays transient**
  `test_a_server_error_stays_transient`
- ✅ **every vendor reports why the model stopped**
  `test_every_vendor_reports_why_the_model_stopped`
- ✅ **i21 a reply cut off at the token limit is marked truncated**
  `test_i21_a_reply_cut_off_at_the_token_limit_is_marked_truncated`
- ✅ **i21 a finished reply is not marked truncated**
  `test_i21_a_finished_reply_is_not_marked_truncated`
- ✅ **i21 a content filter stop fails the run with its own code**
  `test_i21_a_content_filter_stop_fails_the_run_with_its_own_code`
- ✅ **i21 a context overflow is retried once with a smaller prompt**
  `test_i21_a_context_overflow_is_retried_once_with_a_smaller_prompt`
- ✅ **i21 a second overflow is real and fails the run**
  `test_i21_a_second_overflow_is_real_and_fails_the_run`
- ✅ **i21 a rate limit is retried not failed**
  `test_i21_a_rate_limit_is_retried_not_failed`

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
- ✅ **Run a program of journaled operations, kill the attempt after an arbitrary prefix, and let the retry replay. The retry must see exactly the values the first attempt saw for the prefix, and a tool must have run once per call in the program — never again for a call the journal already held.**
  `test_i14_a_replay_makes_the_same_decisions_and_repeats_no_effect`

## structure

- ✅ **The kernel is the engine, so it has to be installable and importable without a vendor SDK, a model runtime, or a database driver.**
  `test_i26_the_kernel_imports_only_its_allowed_third_party_set`
- ✅ **``kernel/testing`` holds conformance suites and doubles. Production code that imported it would make pytest a runtime dependency of the engine.**
  `test_the_kernel_never_imports_its_own_test_support`
- ✅ **``abstractions`` is what an adapter author depends on. If it reaches back into the engine, implementing a port drags the whole engine along.**
  `test_i27_abstractions_never_imports_the_engine`
- ✅ **Every addition or removal in the public API shows up as a diff in ``public_api.json``, so it is reviewed rather than noticed later.**
  `test_i28_the_public_api_matches_its_snapshot`
- ✅ **The row that keeps the other rows honest. A file once promised "the same suite is run against those implementations" and never was, while three backends drifted apart.**
  `test_i30_every_implementation_of_a_port_with_a_suite_runs_it`
- ⏳ **i30 every port has a conformance suite**
  `test_i30_every_port_has_a_conformance_suite`
  _Pending — I30: RuntimeStore, MemoryStore and VectorStore have conformance suites; the other storage, LLM and extractor ports get theirs in step 5 of the kernel rewrite._
- ✅ **The engine instruments itself through ``opentelemetry-api``, which does nothing until a host configures an SDK. The SDK, the exporter and the web-framework instrumentation are the host's choice — the reference server installs them through its extra — so a plain install of the engine does not pull them in.**
  `test_the_core_install_carries_the_opentelemetry_api_and_nothing_that_exports`
- ✅ **The AST check above sees what each file names; this one sees what actually loads. A module that reached a vendor SDK, a logging stack or a database driver through a helper would pass the first and fail this.**
  `test_i26_importing_the_whole_engine_loads_only_the_allowed_third_party_set`

## telemetry

- ✅ **A trace that is four traces cannot answer 'what did this run do', which is the only question it exists to answer.**
  `test_i24_one_turn_is_one_trace`
- ✅ **i24 only the run span is a root**
  `test_i24_only_the_run_span_is_a_root`
- ✅ **A span with no outcome recorded is a timing bar and nothing more.**
  `test_i24_spans_carry_their_outcome`
- ✅ **Telemetry leaves the erasure boundary: it is sampled, exported to third parties and retained on their schedule. Content goes in it only when a deployment explicitly opts in.**
  `test_i25_no_prompt_or_tool_content_appears_in_span_attributes`

## tenancy

- ✅ **Ids come from request bodies and model output. An id alone must never be enough to address a record.**
  `test_i03_a_record_is_not_readable_from_another_tenant`
- ✅ **``save`` is an upsert keyed by id, and the id is caller-supplied.**
  `test_i03_one_tenant_cannot_overwrite_another_tenants_record`
- ✅ **The dangerous default: forgetting a field widens the query instead of narrowing it, and nothing in the type system notices.**
  `test_i03_omitting_a_scope_field_is_not_a_wildcard`
- ✅ **A deletion request has to reach every store, including the journal that holds the raw conversation. Today the GDPR eraser touches neither memory nor the event log.**
  `test_i04_a_scope_can_be_erased_completely`

## tool safety

- ✅ **i22 a tool without a risk is refused**
  `test_i22_a_tool_without_a_risk_is_refused`
- ✅ **i22 a tool without an idempotency declaration is refused**
  `test_i22_a_tool_without_an_idempotency_declaration_is_refused`
- ✅ **``"sensitive"`` is not a level. A string that merely looks right is how a tool ends up outside the approval ordering.**
  `test_i22_a_risk_that_is_not_a_tool_risk_is_refused`
- ✅ **i22 a fully declared tool is accepted**
  `test_i22_a_fully_declared_tool_is_accepted`
- ✅ **i22 every shipped tool declares its risk and idempotency**
  `test_i22_every_shipped_tool_declares_its_risk_and_idempotency`
- ✅ **i22 a tool from an mcp server needs approval unless the operator says otherwise**
  `test_i22_a_tool_from_an_mcp_server_needs_approval_unless_the_operator_says_otherwise`

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

## versioning

- ✅ **i15 a run continues under the version that started it**
  `test_i15_a_run_continues_under_the_version_that_started_it`
- ✅ **i15 a run is refused by a different version**
  `test_i15_a_run_is_refused_by_a_different_version`

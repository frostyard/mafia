export type RequirementType = "issue" | "text";
export type WorkflowType = "specification" | "pull_request_review";

export interface Repository {
  id: string;
  owner: string;
  name: string;
  remote_url: string;
  default_branch: string | null;
  last_fetched_sha: string | null;
}

export interface Run {
  id: string;
  repository: Repository;
  workflow_type: WorkflowType;
  requirement_type: RequirementType | null;
  issue_number: number | null;
  requirement_text: string | null;
  pull_request_number: number | null;
  primary_model: string;
  reviewer_model: string;
  thread_id: string;
  state: string;
  version: number;
  active_spec_revision: number | null;
  active_plan_revision: number | null;
  active_review_revision: number | null;
  failure_code: string | null;
  failure_message: string | null;
  created_at: string;
  updated_at: string;
}

export type ArtifactKind =
  | "specification"
  | "plan"
  | "review"
  | "review_ledger"
  | "phase_result"
  | "implementation_review"
  | "implementation_review_ledger"
  | "remediation_report"
  | "remediation_verification"
  | "pull_request_review"
  | "pull_request_review_consolidated";

export interface Artifact {
  id: string;
  kind: ArtifactKind | string;
  schema_version: number;
  revision: number;
  structured_data: Record<string, unknown>;
  rendered_markdown: string;
  model: string;
  source_snapshot_id: string | null;
  created_at: string;
}

export interface Phase {
  id: string;
  ordinal: number;
  title: string;
  objective: string;
  dependencies: number[];
  details: Record<string, unknown>;
  status: string;
  plan_revision: number;
  source_sha: string;
  branch_name: string | null;
  commit_sha: string | null;
  pr_number: number | null;
  pr_url: string | null;
  merge_sha: string | null;
  review_cycle: number;
  implementation_review_attempts: number;
  remediation_attempts: number;
  verification_attempts: number;
  candidate_base_sha: string | null;
  candidate_diff_hash: string | null;
}

export interface Evidence {
  id: string;
  snapshot_id: string;
  source_sha: string;
  kind: string;
  path_or_url: string;
  line_start: number | null;
  line_end: number | null;
  excerpt_hash: string;
  detail: Record<string, unknown>;
  created_at: string;
}

export interface RunDetail extends Run {
  artifacts: Artifact[];
  phases: Phase[];
}

export type ActivityStatusMode =
  | "idle"
  | "working"
  | "decision"
  | "external"
  | "failed"
  | "cancelled"
  | "completed";

export interface Operation {
  id: string;
  phase_id: string | null;
  operation_type: string;
  status: string;
  model: string | null;
  attempt: number;
  timeout_seconds: number | null;
  detail: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
  started_at: string;
  heartbeat_at: string;
  progress_at: string;
  completed_at: string | null;
  elapsed_seconds: number;
}

export interface ActivityEvent {
  id: string;
  event_type: string;
  from_state: string | null;
  to_state: string | null;
  payload: Record<string, unknown>;
  actor: string;
  created_at: string;
}

export interface RunActivity {
  run_id: string;
  state: string;
  version: number;
  status_mode: ActivityStatusMode;
  status_message: string;
  stalled: boolean;
  stall_reason: string | null;
  stall_threshold_seconds: number;
  can_cancel: boolean;
  can_retry: boolean;
  source_sha: string | null;
  files_discovered: number | null;
  citations_found: number;
  operations: Operation[];
  events: ActivityEvent[];
}

export interface ModelAvailability {
  pairs: ModelPair[];
  required: string[];
  available: string[];
  missing: string[];
}

export interface ModelPair {
  primary_model: string;
  reviewer_model: string;
}

export interface RunCreate {
  workflow_type: WorkflowType;
  repository: string;
  primary_model: PrimaryModel;
  issue_number?: number;
  requirement_text?: string;
  pull_request_number?: number;
}

export type PrimaryModel = string;

export interface ApiError {
  code?: string;
  message: string;
}

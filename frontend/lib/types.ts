export type DemoRole =
  | "patient"
  | "dpo"
  | "admin"
  | "department_contributor"
  | "independent_reviewer";

export interface DemoSession {
  role: DemoRole;
  principalId: string;
}

export interface DSRRequest {
  id: number;
  request_type: string;
  request_type_label?: string;
  description: string;
  department: string;
  state: string;
  assigned_to: string;
  resolution_status: string;
  sub_status?: string;
  legal_review_completed?: boolean;
  proposed_outcome?: string | null;
  independent_review_required?: boolean;
  independent_review_reason?: string;
  reconsideration_of?: number | null;
  case_stage?: string;
  escalation_active?: boolean;
  waiting_for_subject_information?: boolean;
  disposition_initiator?: string | null;
  jurisdiction_code?: string;
  submitted_at: string;
  response_due_at: string;
  legal_deadline_at: string | null;
  legal_time_remaining_seconds: number | null;
  requires_patient_response: boolean;
  duplicate_of?: number | null;
  time_remaining_seconds: number;
  closed_at: string | null;
}

export interface DPOQueueResponse {
  requests: DSRRequest[];
  departmental_heatmap: Record<string, number>;
  filters: {
    state_filter: string;
    type_filter: string;
    assignee_filter: string;
    queue_filter: string;
  };
}

export interface DSRDeadlinePolicy {
  id: number;
  request_type: string;
  request_type_label: string;
  jurisdiction_code: string;
  version: number;
  effective_at: string;
  internal_target_days: number;
  legal_deadline_days: number | null;
  policy_source: string;
  extension_allowed: boolean;
  maximum_extension_days: number;
  pause_allowed: boolean;
  pause_reasons: string[];
  notification_required: boolean;
  updated_by: string;
  updated_at: string;
}

export interface DSRCaseActivity {
  kind: "transition" | "case";
  actor_id: string;
  summary: string;
  timestamp: string;
}

export interface DSRTransitionAction {
  label: string;
  action: string;
}

export interface DSRCaseDetailResponse {
  request: DSRRequest;
  activity: DSRCaseActivity[];
  available_actions: DSRTransitionAction[];
  communications: PatientCommunication[];
  legal_reviews: DSRLegalReview[];
  work_items: DSRWorkItem[];
  escalations: DSREscalation[];
  exceptions: DSRCaseException[];
  review_material?: {
    response_version: number;
    response_content: string;
    case_snapshot: Record<string, unknown>;
    source_searches: Array<Record<string, unknown>>;
    decisions: Array<Record<string, unknown>>;
  };
}

export interface DSRWorkItem {
  id: number;
  title: string;
  task_type: string;
  details: string;
  assigned_to: string;
  due_at: string | null;
  required_for_completion: boolean;
  completion_summary: string;
  status: string;
}

export interface DSREscalation {
  id: number;
  reason: string;
  opened_by: string;
  assigned_to: string;
  due_at: string | null;
  resolution: string;
  resolved_at: string | null;
  active: boolean;
}

export interface DSRCaseException {
  id: number;
  exception_type: string;
  reason: string;
  actor_id: string;
  patient_notified_at: string | null;
  deadline_effect_seconds: number;
  created_at: string;
}

export interface DSRAssignedReview {
  request_id: number;
  request_type: string;
  case_stage: string;
  requested_by: string;
  request_reason: string;
  proposed_outcome: string;
  response_version: number;
  requested_at: string;
}

export interface DSRLegalReview {
  id: number;
  requested_by: string;
  request_reason: string;
  proposed_outcome: string;
  response_version: number;
  requested_at: string;
  decision?: {
    reviewer_id: string;
    outcome: string;
    rationale: string;
    decided_at: string;
  };
}

export interface PatientRequestDetailResponse extends DSRRequest {
  can_withdraw: boolean;
  communications: PatientCommunication[];
  published_responses: PublishedResponse[];
}

export interface PatientCommunication {
  id: number;
  message: string;
  direction: string;
  created_by: string;
  created_at: string;
}

export interface PublishedResponse {
  version: number;
  content: string;
  publication: {
    published_by: string;
    published_at: string;
  };
}

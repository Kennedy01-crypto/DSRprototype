export type DemoRole = "patient" | "dpo" | "admin";

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
  request_type: string;
  request_type_label: string;
  internal_target_days: number;
  legal_deadline_days: number | null;
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

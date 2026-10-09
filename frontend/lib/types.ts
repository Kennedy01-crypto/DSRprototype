export type DemoRole = "patient" | "dpo";

export interface DemoSession {
  role: DemoRole;
  principalId: string;
}

export interface DSRRequest {
  id: number;
  request_type: string;
  description: string;
  department: string;
  state: string;
  assigned_to: string;
  resolution_status: string;
  submitted_at: string;
  response_due_at: string;
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
}

export interface PatientRequestDetailResponse extends DSRRequest {
  can_withdraw: boolean;
}

import { proxyDpo } from "@/lib/dpo-proxy";

const STATUSES = new Set(["PENDING", "RUNNING", "SUCCEEDED", "FAILED"]);

export async function GET(request: Request) {
  const incoming = new URL(request.url).searchParams;
  const params = new URLSearchParams();
  const status = incoming.get("status") ?? "";
  if (STATUSES.has(status)) params.set("status", status);
  const requestId = incoming.get("request") ?? "";
  if (/^[1-9]\d*$/.test(requestId)) params.set("request", requestId);
  const query = params.size ? `?${params.toString()}` : "";
  return proxyDpo(`/api/v1/dpo/integration-jobs/${query}`, "GET");
}

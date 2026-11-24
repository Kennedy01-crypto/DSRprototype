import { invalidIdResponse, isRecordId, proxyDpo } from "@/lib/dpo-proxy";

type RouteContext = { params: Promise<{ jobId: string }> };

export async function POST(_request: Request, context: RouteContext) {
  const { jobId } = await context.params;
  if (!isRecordId(jobId)) return invalidIdResponse();
  return proxyDpo(`/api/v1/dpo/integration-jobs/${jobId}/retry/`, "POST");
}

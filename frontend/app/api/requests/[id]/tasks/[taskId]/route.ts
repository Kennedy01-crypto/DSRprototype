import { invalidIdResponse, isRecordId, proxyDpo } from "@/lib/dpo-proxy";

type RouteContext = { params: Promise<{ id: string; taskId: string }> };

export async function PATCH(request: Request, context: RouteContext) {
  const { id, taskId } = await context.params;
  if (!isRecordId(id, taskId)) return invalidIdResponse();
  return proxyDpo(`/api/v1/dpo/dsrs/${id}/tasks/${taskId}/`, "PATCH", request);
}

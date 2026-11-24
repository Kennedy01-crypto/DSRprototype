import { invalidIdResponse, isRecordId, proxyDpo } from "@/lib/dpo-proxy";

type RouteContext = { params: Promise<{ id: string }> };

export async function PATCH(request: Request, context: RouteContext) {
  const { id } = await context.params;
  if (!isRecordId(id)) return invalidIdResponse();
  return proxyDpo(`/api/v1/dpo/dsrs/${id}/jurisdiction/`, "PATCH", request);
}

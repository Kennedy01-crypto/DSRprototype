import { invalidIdResponse, isRecordId, proxyDpo } from "@/lib/dpo-proxy";

type RouteContext = { params: Promise<{ id: string; searchId: string }> };

export async function PATCH(request: Request, context: RouteContext) {
  const { id, searchId } = await context.params;
  if (!isRecordId(id, searchId)) return invalidIdResponse();
  return proxyDpo(`/api/v1/dpo/dsrs/${id}/source-searches/${searchId}/`, "PATCH", request);
}

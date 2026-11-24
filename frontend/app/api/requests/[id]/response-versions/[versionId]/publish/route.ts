import { invalidIdResponse, isRecordId, proxyDpo } from "@/lib/dpo-proxy";

type RouteContext = { params: Promise<{ id: string; versionId: string }> };

export async function POST(_request: Request, context: RouteContext) {
  const { id, versionId } = await context.params;
  if (!isRecordId(id, versionId)) return invalidIdResponse();
  return proxyDpo(
    `/api/v1/dpo/dsrs/${id}/response-versions/${versionId}/publish/`,
    "POST",
  );
}

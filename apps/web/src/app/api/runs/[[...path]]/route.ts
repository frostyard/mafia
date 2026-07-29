import { proxyApiRequest } from "@/lib/api-proxy";

interface RouteContext {
  params: Promise<{ path?: string[] }>;
}

async function proxyRunRequest(
  request: Request,
  context: RouteContext,
): Promise<Response> {
  const { path = [] } = await context.params;
  const suffix = path.map(encodeURIComponent).join("/");
  return proxyApiRequest(
    request,
    `/api/runs${suffix ? `/${suffix}` : ""}`,
  );
}

export const GET = proxyRunRequest;
export const POST = proxyRunRequest;

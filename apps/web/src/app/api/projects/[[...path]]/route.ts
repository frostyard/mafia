import { proxyApiRequest } from "@/lib/api-proxy";

interface RouteContext {
  params: Promise<{ path?: string[] }>;
}

async function proxyProjectRequest(
  request: Request,
  context: RouteContext,
): Promise<Response> {
  const { path = [] } = await context.params;
  const suffix = path.map(encodeURIComponent).join("/");
  return proxyApiRequest(
    request,
    `/api/projects${suffix ? `/${suffix}` : ""}`,
  );
}

export const GET = proxyProjectRequest;
export const POST = proxyProjectRequest;
export const PUT = proxyProjectRequest;

import { proxyApiRequest } from "@/lib/api-proxy";

interface RouteContext {
  params: Promise<{ path: string[] }>;
}

async function proxyAuthRequest(
  request: Request,
  context: RouteContext,
): Promise<Response> {
  const { path } = await context.params;
  return proxyApiRequest(
    request,
    `/auth/${path.map(encodeURIComponent).join("/")}`,
  );
}

export const GET = proxyAuthRequest;
export const POST = proxyAuthRequest;

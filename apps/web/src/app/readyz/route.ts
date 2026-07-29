import { proxyApiRequest } from "@/lib/api-proxy";

export function GET(request: Request): Promise<Response> {
  return proxyApiRequest(request, "/readyz");
}

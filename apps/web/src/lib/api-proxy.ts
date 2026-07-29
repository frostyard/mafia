const excludedRequestHeaders = new Set([
  "connection",
  "content-length",
  "host",
  "transfer-encoding",
]);

export async function proxyApiRequest(
  request: Request,
  path: string,
): Promise<Response> {
  const apiUrl = process.env.MAFIA_API_URL ?? "http://127.0.0.1:8000";
  const target = new URL(path, `${apiUrl.replace(/\/+$/, "")}/`);
  target.search = new URL(request.url).search;
  const headers = new Headers(request.headers);
  excludedRequestHeaders.forEach((header) => headers.delete(header));
  headers.delete("x-mafia-operator-id");
  headers.delete("x-mafia-operator-login");
  const internalSecret = process.env.MAFIA_INTERNAL_SECRET;
  if (internalSecret) {
    headers.set("X-Mafia-Internal-Secret", internalSecret);
  }
  if (process.env.MAFIA_AUTH_MODE?.toLowerCase() === "github") {
    const operatorId = request.headers.get("x-mafia-github-user-id");
    const operatorLogin = request.headers.get("x-mafia-github-login");
    if (operatorId && operatorLogin) {
      headers.set("X-Mafia-Operator-ID", operatorId);
      headers.set("X-Mafia-Operator-Login", operatorLogin);
    }
  }
  const body =
    request.method === "GET" || request.method === "HEAD"
      ? undefined
      : await request.arrayBuffer();

  try {
    const response = await fetch(target, {
      body,
      cache: "no-store",
      headers,
      method: request.method,
      redirect: "manual",
    });
    return new Response(response.body, {
      headers: response.headers,
      status: response.status,
      statusText: response.statusText,
    });
  } catch (error) {
    return Response.json(
      {
        detail: {
          code: "api_unavailable",
          message:
            error instanceof Error
              ? `Unable to reach the MAFIA API: ${error.message}`
              : "Unable to reach the MAFIA API.",
        },
      },
      { status: 502 },
    );
  }
}

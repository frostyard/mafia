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

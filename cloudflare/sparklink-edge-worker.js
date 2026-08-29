/*
 * SparkLink edge boundary.
 *
 * Configure CONTROL_PLANE_ORIGIN as the private origin URL exposed through a
 * hostname-scoped, Strict-TLS route. The Worker intentionally keeps
 * Subscription tokens out of the upstream path and forwards them as an
 * internal subscription-token header.
 */

const NO_STORE = {
  "Cache-Control": "no-store, private",
  "X-Content-Type-Options": "nosniff",
};

function withNoStore(response) {
  const headers = new Headers(response.headers);
  for (const [key, value] of Object.entries(NO_STORE)) headers.set(key, value);
  return new Response(response.body, { status: response.status, headers });
}

async function forward(request, env, path, token = null) {
  const origin = new URL(env.CONTROL_PLANE_ORIGIN);
  origin.pathname = `/sparklink-mvp${path}`;
  origin.search = new URL(request.url).search;
  const headers = new Headers(request.headers);
  if (token) {
    headers.delete("Authorization");
    headers.set("X-SparkLink-Subscription-Token", token);
  }
  const upstream = new Request(origin, { method: request.method, headers, body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body });
  return withNoStore(await fetch(upstream, { cf: { cacheTtl: 0, cacheEverything: false } }));
}

async function legacyPlanResponse(request, env, key) {
  if (!env.SUBSCRIPTIONS) return new Response("legacy delivery unavailable\n", { status: 503, headers: NO_STORE });
  const body = await env.SUBSCRIPTIONS.get(key);
  if (!body) return new Response("legacy plan not found\n", { status: 404, headers: NO_STORE });
  return new Response(body, { status: 200, headers: { ...NO_STORE, "Content-Type": "text/plain; charset=utf-8" } });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.hostname === "sub.enrpiglink.top") {
      if (request.method !== "GET") return new Response("method not allowed\n", { status: 405, headers: NO_STORE });
      if (url.pathname.startsWith("/u/")) {
        const token = decodeURIComponent(url.pathname.slice(3));
        if (!token || token.includes("/")) return new Response("not found\n", { status: 404, headers: NO_STORE });
        return forward(request, env, "/subscription", token);
      }
      if (env.BASIC_PATH && url.pathname === env.BASIC_PATH) return legacyPlanResponse(request, env, "basic");
      if (env.PLUS_PATH && url.pathname === env.PLUS_PATH) return legacyPlanResponse(request, env, "plus");
      return new Response("not found\n", { status: 404, headers: NO_STORE });
    }

    if (url.hostname === "spark.enrpiglink.top") {
      return forward(request, env, url.pathname || "/");
    }

    return new Response("not found\n", { status: 404, headers: NO_STORE });
  },
};

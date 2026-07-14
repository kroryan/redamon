/**
 * Single source of truth for every browser -> agent WebSocket URL: the chat
 * socket (`/ws/agent`), the Kali terminal, and both cypherfix sockets. Keeping
 * one implementation means one code path to reason about and to test.
 *
 * Resolution order:
 *
 *  1. `NEXT_PUBLIC_AGENT_WS_URL` -- baked at build time by the single-origin
 *     deploy (deploy.sh computes `wss://<host>/ws/agent`). The value always ends
 *     in `/ws/agent`; we swap that suffix for the caller's path.
 *
 *  2. Browser auto-detect (no env var -- local dev, or a build where the ARG was
 *     not passed). `localhost`/`127.0.0.1` talk straight to the agent on `:8090`
 *     (the dev topology: UI on :3000, agent on :8090). ANY other host reuses the
 *     current page origin (`host[:port]`) so whatever reverse proxy served the
 *     page (nginx on 80/443) also routes `/ws/*`. We deliberately do NOT hardcode
 *     a public `:8090` here: in a hardened deploy the agent port is loopback-bound
 *     and unreachable from the browser, so a `:8090` fallback could only ever fail.
 *
 *  3. SSR fallback (no `window`) -- dev-only `localhost:8090`; never reaches a real
 *     browser (the socket is opened client-side).
 *
 * @param path   WS path to target, e.g. `/ws/kali-terminal`. Should start `/ws/`.
 * @param ticket optional ws-ticket, appended as a `ticket` query param (STRIDE S3/S4).
 */
export function buildAgentWsUrl(path: string, ticket?: string): string {
  let base: string
  const configured = process.env.NEXT_PUBLIC_AGENT_WS_URL
  if (configured) {
    base = configured.replace(/\/ws\/agent$/, path)
  } else if (typeof window !== 'undefined') {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = window.location.hostname
    const port = window.location.port
    const isLocal = host === 'localhost' || host === '127.0.0.1'
    const authority = isLocal ? `${host}:8090` : port ? `${host}:${port}` : host
    base = `${protocol}//${authority}${path}`
  } else {
    base = `ws://localhost:8090${path}`
  }
  if (ticket) {
    base += (base.includes('?') ? '&' : '?') + 'ticket=' + encodeURIComponent(ticket)
  }
  return base
}

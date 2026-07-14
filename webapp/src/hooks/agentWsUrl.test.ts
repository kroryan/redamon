import { describe, test, expect, beforeEach, afterEach, vi } from 'vitest'
import { buildAgentWsUrl } from './agentWsUrl'

/**
 * Regression suite for the browser -> agent WebSocket URL builder. This exercises
 * the REAL shipped function (not a re-implementation), so a future edit that
 * reintroduces the hardcoded public `:8090` -- the exact bug behind the
 * "Connecting to kali-sandbox..." forever report -- fails here.
 *
 * The four WS hooks (chat /ws/agent, kali terminal, both cypherfix sockets) all
 * delegate to buildAgentWsUrl, so covering it covers all four.
 */

const ENV_KEY = 'NEXT_PUBLIC_AGENT_WS_URL'

// Point buildAgentWsUrl at a synthetic browser location. Uses vi.stubGlobal so
// each case is isolated and restored by vi.unstubAllGlobals() in afterEach.
function stubLocation(loc: { protocol: string; hostname: string; port: string }) {
  vi.stubGlobal('window', { location: loc })
}

beforeEach(() => {
  delete process.env[ENV_KEY]
})

afterEach(() => {
  vi.unstubAllGlobals()
  delete process.env[ENV_KEY]
})

const PATHS = [
  '/ws/agent',
  '/ws/kali-terminal',
  '/ws/cypherfix-triage',
  '/ws/cypherfix-codefix',
] as const

describe('buildAgentWsUrl -- NEXT_PUBLIC_AGENT_WS_URL baked (deploy.sh single-origin)', () => {
  test('swaps the /ws/agent suffix for the caller path (wss domain)', () => {
    process.env[ENV_KEY] = 'wss://redamon.example.com/ws/agent'
    expect(buildAgentWsUrl('/ws/kali-terminal')).toBe('wss://redamon.example.com/ws/kali-terminal')
    expect(buildAgentWsUrl('/ws/cypherfix-triage')).toBe('wss://redamon.example.com/ws/cypherfix-triage')
    expect(buildAgentWsUrl('/ws/cypherfix-codefix')).toBe('wss://redamon.example.com/ws/cypherfix-codefix')
  })

  test('/ws/agent path is a no-op replace, returns the configured URL verbatim', () => {
    process.env[ENV_KEY] = 'wss://redamon.example.com/ws/agent'
    expect(buildAgentWsUrl('/ws/agent')).toBe('wss://redamon.example.com/ws/agent')
  })

  test('honours ws:// (http-mode deploy)', () => {
    process.env[ENV_KEY] = 'ws://redamon.example.com/ws/agent'
    expect(buildAgentWsUrl('/ws/kali-terminal')).toBe('ws://redamon.example.com/ws/kali-terminal')
  })

  test('the env branch never injects a :8090 port', () => {
    process.env[ENV_KEY] = 'wss://redamon.example.com/ws/agent'
    for (const p of PATHS) {
      expect(buildAgentWsUrl(p)).not.toContain(':8090')
    }
  })

  test('env branch wins even when a window is present', () => {
    process.env[ENV_KEY] = 'wss://redamon.example.com/ws/agent'
    stubLocation({ protocol: 'https:', hostname: 'someotherhost', port: '' })
    expect(buildAgentWsUrl('/ws/kali-terminal')).toBe('wss://redamon.example.com/ws/kali-terminal')
  })
})

describe('buildAgentWsUrl -- browser auto-detect, local dev keeps :8090', () => {
  test('localhost targets the agent on :8090 (ws)', () => {
    stubLocation({ protocol: 'http:', hostname: 'localhost', port: '3000' })
    expect(buildAgentWsUrl('/ws/agent')).toBe('ws://localhost:8090/ws/agent')
    expect(buildAgentWsUrl('/ws/kali-terminal')).toBe('ws://localhost:8090/ws/kali-terminal')
  })

  test('127.0.0.1 targets the agent on :8090', () => {
    stubLocation({ protocol: 'http:', hostname: '127.0.0.1', port: '3000' })
    expect(buildAgentWsUrl('/ws/cypherfix-triage')).toBe('ws://127.0.0.1:8090/ws/cypherfix-triage')
  })
})

describe('buildAgentWsUrl -- browser auto-detect, proxied deploy uses same origin (the fix)', () => {
  test('custom domain over https -> wss same-origin, NO :8090', () => {
    stubLocation({ protocol: 'https:', hostname: 'redamon.pentest.megaleo.com', port: '' })
    const url = buildAgentWsUrl('/ws/kali-terminal')
    expect(url).toBe('wss://redamon.pentest.megaleo.com/ws/kali-terminal')
    expect(url).not.toContain(':8090')
  })

  test('custom domain over http -> ws same-origin, NO :8090', () => {
    stubLocation({ protocol: 'http:', hostname: 'redamon.pentest.megaleo.com', port: '' })
    const url = buildAgentWsUrl('/ws/agent')
    expect(url).toBe('ws://redamon.pentest.megaleo.com/ws/agent')
    expect(url).not.toContain(':8090')
  })

  test('non-default proxy port is preserved (e.g. :8443)', () => {
    stubLocation({ protocol: 'https:', hostname: 'redamon.example.com', port: '8443' })
    expect(buildAgentWsUrl('/ws/cypherfix-codefix')).toBe(
      'wss://redamon.example.com:8443/ws/cypherfix-codefix',
    )
  })

  test('bare public IP host reuses the origin, not :8090', () => {
    stubLocation({ protocol: 'https:', hostname: '203.0.113.7', port: '' })
    const url = buildAgentWsUrl('/ws/agent')
    expect(url).toBe('wss://203.0.113.7/ws/agent')
    expect(url).not.toContain(':8090')
  })

  test('no WS path across the four sockets leaks :8090 on a proxied host', () => {
    stubLocation({ protocol: 'https:', hostname: 'redamon.pentest.megaleo.com', port: '' })
    for (const p of PATHS) {
      expect(buildAgentWsUrl(p)).not.toContain(':8090')
    }
  })
})

describe('buildAgentWsUrl -- SSR fallback (no window)', () => {
  test('returns dev localhost:8090 when window is undefined', () => {
    vi.stubGlobal('window', undefined)
    expect(buildAgentWsUrl('/ws/agent')).toBe('ws://localhost:8090/ws/agent')
  })
})

describe('buildAgentWsUrl -- ticket query param (STRIDE S3/S4)', () => {
  test('appends ticket with ? on a portless proxied URL', () => {
    stubLocation({ protocol: 'https:', hostname: 'redamon.example.com', port: '' })
    expect(buildAgentWsUrl('/ws/kali-terminal', 'abc.def.ghi')).toBe(
      'wss://redamon.example.com/ws/kali-terminal?ticket=abc.def.ghi',
    )
  })

  test('URL-encodes ticket values that contain reserved chars', () => {
    process.env[ENV_KEY] = 'wss://redamon.example.com/ws/agent'
    const url = buildAgentWsUrl('/ws/cypherfix-triage', 'a b+c/d=e')
    expect(url).toBe('wss://redamon.example.com/ws/cypherfix-triage?ticket=a%20b%2Bc%2Fd%3De')
  })

  test('no ticket -> no query string', () => {
    stubLocation({ protocol: 'http:', hostname: 'localhost', port: '3000' })
    expect(buildAgentWsUrl('/ws/agent')).not.toContain('ticket=')
  })

  test('empty-string ticket is treated as absent (falsy)', () => {
    stubLocation({ protocol: 'http:', hostname: 'localhost', port: '3000' })
    expect(buildAgentWsUrl('/ws/agent', '')).toBe('ws://localhost:8090/ws/agent')
  })
})

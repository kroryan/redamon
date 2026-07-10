/**
 * S2/E2 — internal-key route allowlist (middleware scoping).
 * Run: npx vitest run src/middleware.allowlist.test.ts
 * @vitest-environment node
 */
import { describe, test, expect } from 'vitest'
import { internalKeyRouteAllowed } from './middleware'

describe('internalKeyRouteAllowed — enumerated internal routes pass', () => {
  test.each([
    ['GET', '/api/users/abc/llm-providers'],
    ['GET', '/api/users/abc/settings'],
    ['GET', '/api/users/abc/tradecraft-resources'],
    ['GET', '/api/projects/p1'],
    ['POST', '/api/internal/codefix-sandbox/job1/exec'],
    ['GET', '/api/conversations/by-session/s1'],
    ['POST', '/api/conversations/by-session/s1/messages'],
    ['POST', '/api/remediations'],
    ['POST', '/api/remediations/batch'],
    ['PATCH', '/api/remediations/r1'],
    ['GET', '/api/global/tunnel-config'],
  ])('%s %s → allowed', (method, path) => {
    expect(internalKeyRouteAllowed(method, path)).toBe(true)
  })
})

describe('internalKeyRouteAllowed — off-allowlist routes are NOT allowed', () => {
  test.each([
    ['POST', '/api/users'],                       // mint-admin path
    ['PUT', '/api/users/abc'],                     // user update
    ['DELETE', '/api/users/abc'],                  // user delete
    ['POST', '/api/users/abc/llm-providers'],      // add provider (write)
    ['GET', '/api/users'],                         // list all users
    ['GET', '/api/analytics/redzone'],             // arbitrary route
    ['POST', '/api/users/abc/settings'],           // settings is GET-only for the key
    ['GET', '/api/projects'],                      // projects LIST (only /[id] allowed)
  ])('%s %s → NOT allowed', (method, path) => {
    expect(internalKeyRouteAllowed(method, path)).toBe(false)
  })
})

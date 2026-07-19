import { NextRequest, NextResponse } from 'next/server'
import prisma from '@/lib/prisma'
import { requireEffectiveUser, requireProjectAccess } from '@/lib/access'
import type { Prisma } from '@prisma/client'

// Summary columns only — bodies are fetched on demand via the [id] detail route,
// so a large history never materializes in the list response.
const LIST_SELECT = {
  id: true,
  startedAt: true,
  source: true,
  runId: true,
  sessionId: true,
  tool: true,
  phase: true,
  method: true,
  scheme: true,
  host: true,
  port: true,
  path: true,
  query: true,
  statusCode: true,
  respBodySize: true,
  respContentType: true,
  responseTimeMs: true,
  isTls: true,
  isReplay: true,
  hasSetCookie: true,
  hadAuth: true,
  reflectedParams: true,
  blocked: true,
  inScope: true,
} satisfies Prisma.CapturedHttpTransactionSelect

// Whitelisted sortable columns (prevents arbitrary orderBy injection).
const SORTABLE = new Set([
  'startedAt', 'statusCode', 'host', 'tool', 'method', 'respBodySize', 'responseTimeMs', 'source',
])

const MAX_PAGE_SIZE = 200

function statusClassRange(cls: string | null): Prisma.IntFilter | undefined {
  switch (cls) {
    case '2xx': return { gte: 200, lt: 300 }
    case '3xx': return { gte: 300, lt: 400 }
    case '4xx': return { gte: 400, lt: 500 }
    case '5xx': return { gte: 500, lt: 600 }
    default: return undefined
  }
}

// Build the tenant-scoped where clause shared by list + (future) export/delete.
// eff/projectId are derived from the session + route, never from client fields.
export function buildTrafficWhere(
  projectId: string,
  userId: string,
  sp: URLSearchParams,
): Prisma.CapturedHttpTransactionWhereInput {
  const where: Prisma.CapturedHttpTransactionWhereInput = { projectId, userId }

  const source = sp.get('source')
  if (source && source !== 'both') where.source = source

  const tool = sp.get('tool')
  if (tool) where.tool = { in: tool.split(',').map(t => t.trim()).filter(Boolean) }

  const sessionId = sp.get('sessionId')
  if (sessionId) where.sessionId = sessionId

  const runId = sp.get('runId')
  if (runId) where.runId = runId

  const host = sp.get('host')
  if (host) where.host = host

  const method = sp.get('method')
  if (method) where.method = method

  const statusRange = statusClassRange(sp.get('statusClass'))
  if (statusRange) where.statusCode = statusRange

  // Date range over startedAt (native <input type=date> yields YYYY-MM-DD).
  // Ignore unparseable values rather than letting an Invalid Date reach Prisma
  // (which would 500 the request).
  const from = sp.get('from')
  const to = sp.get('to')
  const startedAt: Prisma.DateTimeFilter = {}
  if (from) {
    const d = new Date(from)
    if (!Number.isNaN(d.getTime())) startedAt.gte = d
  }
  if (to) {
    const end = new Date(to)
    if (!Number.isNaN(end.getTime())) {
      end.setUTCHours(23, 59, 59, 999) // inclusive end-of-day
      startedAt.lte = end
    }
  }
  if (startedAt.gte || startedAt.lte) where.startedAt = startedAt

  // Quick toggles
  if (sp.get('hasSetCookie') === 'true') where.hasSetCookie = true
  if (sp.get('reflected') === 'true') where.reflectedParams = true
  if (sp.get('only5xx') === 'true') where.statusCode = { gte: 500, lt: 600 }

  // Free-text over URL (host/path). Body FTS is Phase 3.
  const q = sp.get('q')
  if (q) {
    where.OR = [
      { host: { contains: q, mode: 'insensitive' } },
      { path: { contains: q, mode: 'insensitive' } },
    ]
  }

  return where
}

// GET /api/traffic/[projectId] — paginated, filtered transaction list.
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ projectId: string }> },
) {
  try {
    const { projectId } = await params

    const eff = await requireEffectiveUser()
    if (eff instanceof NextResponse) return eff
    const access = await requireProjectAccess(eff, projectId)
    if (access instanceof NextResponse) return access

    const sp = request.nextUrl.searchParams
    const where = buildTrafficWhere(projectId, eff.userId, sp)

    const page = Math.max(0, parseInt(sp.get('page') || '0', 10) || 0)
    const pageSize = Math.min(MAX_PAGE_SIZE, Math.max(1, parseInt(sp.get('pageSize') || '50', 10) || 50))

    const sortField = sp.get('sort') || 'startedAt'
    const orderBy: Prisma.CapturedHttpTransactionOrderByWithRelationInput = SORTABLE.has(sortField)
      ? { [sortField]: sp.get('dir') === 'asc' ? 'asc' : 'desc' }
      : { startedAt: 'desc' }

    const [rows, total] = await Promise.all([
      prisma.capturedHttpTransaction.findMany({
        where,
        select: LIST_SELECT,
        orderBy,
        skip: page * pageSize,
        take: pageSize,
      }),
      prisma.capturedHttpTransaction.count({ where }),
    ])

    return NextResponse.json({ rows, total, page, pageSize })
  } catch (error) {
    console.error('Failed to list captured traffic:', error)
    return NextResponse.json({ error: 'Failed to list traffic' }, { status: 500 })
  }
}

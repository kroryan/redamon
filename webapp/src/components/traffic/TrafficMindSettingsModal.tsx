'use client'

/**
 * TrafficMind global settings, as a self-contained admin modal opened from the
 * TrafficMind (/traffic) page toolbar. It owns its own fetch + save against
 * /api/users/[userId]/settings (capture fields only), so it does not share state
 * with the System settings page and cannot be clobbered by a stale save there.
 *
 * There is a single shared capture proxy, so these are GLOBAL settings: a change
 * applies to every user and project and the last save wins. Admin-only (the
 * opener gates on isAdmin; the settings route also strips capture writes from
 * non-admins).
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Info } from 'lucide-react'
import { Modal } from '@/components/ui/Modal/Modal'
import { Toggle, Tooltip, WikiInfoButton, useToast } from '@/components/ui'
import { TrafficMindProjectMatrix } from '@/components/traffic/TrafficMindProjectMatrix'
import {
  BODY_POLICIES, BODY_RULES_RECOMMENDED, type BodyPolicy, type BodyFamily,
} from '@/lib/captureBodyRules'
import styles from '@/components/settings/Settings.module.css'

// ── Egress-guard toggles (mirror capture_proxy/egress.py). All default true (block).
type EgressToggleKey =
  | 'captureEgressBlockEmptyHost' | 'captureEgressBlockHardGuardrail' | 'captureEgressFailClosed'
  | 'captureEgressBlockUnresolvable' | 'captureEgressBlockPrivate' | 'captureEgressBlockLoopback'
  | 'captureEgressBlockLinkLocal' | 'captureEgressBlockCgnat' | 'captureEgressBlockReserved'
  | 'captureEgressBlockMulticast' | 'captureEgressBlockUnspecified'

const EGRESS_TOGGLES: { key: EgressToggleKey; title: string; tip: string; danger?: boolean }[] = [
  { key: 'captureEgressBlockPrivate', title: 'Private IPs (RFC1918)', danger: true,
    tip: 'Refuse 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 and IPv6 ULA (fc00::/7). Turn OFF to let the proxy reach internal / lab targets on a private network (e.g. Docker 172.x). WARNING: this also exposes RedAmon’s own services on those ranges; keep their IPs in the always-on blocked-IPs denylist (CAPTURE_BLOCKED_IPS) so they stay refused.' },
  { key: 'captureEgressBlockLinkLocal', title: 'Link-local IPs',
    tip: 'Refuse 169.254.0.0/16 and fe80::/10. Includes the cloud metadata endpoint 169.254.169.254, a classic SSRF credential-theft target. Leave on unless you specifically need it.' },
  { key: 'captureEgressBlockLoopback', title: 'Loopback IPs',
    tip: 'Refuse 127.0.0.0/8 and ::1 (the proxy host itself). Prevents the proxy being used to reach services bound to localhost inside its own container.' },
  { key: 'captureEgressBlockCgnat', title: 'CGNAT (100.64/10)',
    tip: 'Refuse 100.64.0.0/10, the carrier-grade NAT shared address space.' },
  { key: 'captureEgressBlockReserved', title: 'Reserved ranges',
    tip: 'Refuse IANA-reserved address ranges (e.g. 240.0.0.0/4 and other non-routable reserved blocks).' },
  { key: 'captureEgressBlockMulticast', title: 'Multicast',
    tip: 'Refuse 224.0.0.0/4 and ff00::/8 multicast destinations.' },
  { key: 'captureEgressBlockUnspecified', title: 'Unspecified (0.0.0.0 / ::)',
    tip: 'Refuse the unspecified addresses 0.0.0.0 and :: .' },
  { key: 'captureEgressBlockHardGuardrail', title: 'Sensitive hostnames',
    tip: 'Refuse .gov / .mil / .edu / .int domains and the built-in hard-guardrail denylist. Stops the proxy reaching protected / government hosts.' },
  { key: 'captureEgressBlockUnresolvable', title: 'Unresolvable hosts',
    tip: 'Refuse hosts that do not resolve in DNS or have an invalid IDNA label. Structural: with no IP there is nothing to connect to, so these are never forwarded regardless of this toggle.' },
  { key: 'captureEgressBlockEmptyHost', title: 'Empty host',
    tip: 'Refuse requests with no Host. Structural: there is no target to forward to, so these are never sent regardless of this toggle.' },
  { key: 'captureEgressFailClosed', title: 'Fail closed on guard error', danger: true,
    tip: 'If the egress guard itself throws an error, refuse the request (fail closed, safe). Turning this OFF makes it FAIL-OPEN: the request is forwarded WITHOUT vetting or IP pinning. Dangerous; leave on.' },
]

// ── Body-storage policy matrix (mirror capture_lib.DEFAULT_BODY_RULES).
const BODY_FAMILIES: { key: BodyFamily; title: string; tip: string }[] = [
  { key: 'text', title: 'HTML / CSS / text', tip: 'text/html, text/css, text/plain, xml, csv.' },
  { key: 'json', title: 'JSON / API data', tip: 'application/json, form-urlencoded, graphql — the highest-value bodies.' },
  { key: 'script', title: 'Scripts (JS)', tip: 'application/javascript, ecmascript.' },
  { key: 'image', title: 'Images', tip: 'image/* — usually render noise.' },
  { key: 'font', title: 'Fonts', tip: 'font/*, woff/woff2/ttf/otf (incl. octet-stream mislabeled by extension).' },
  { key: 'video', title: 'Video', tip: 'video/* — large render noise.' },
  { key: 'audio', title: 'Audio', tip: 'audio/*.' },
  { key: 'document', title: 'Documents', tip: 'pdf / office / rtf — often leak evidence; agent cannot read, human can.' },
  { key: 'archive', title: 'Archives', tip: 'zip / gzip / tar / 7z — source or backup disclosure.' },
  { key: 'binary', title: 'Binary / downloads', tip: 'octet-stream / wasm / serialized blobs — deserialization & file downloads.' },
]
const BODY_RULES_MINIMAL: Record<string, BodyPolicy> = {
  text: 'auto', json: 'auto', script: 'meta', image: 'meta', font: 'meta',
  video: 'meta', audio: 'meta', document: 'meta', archive: 'meta', binary: 'meta',
}
const BODY_RULES_EVERYTHING: Record<string, BodyPolicy> = {
  text: 'auto', json: 'auto', script: 'auto', image: 'disk', font: 'disk',
  video: 'disk', audio: 'disk', document: 'disk', archive: 'disk', binary: 'disk',
}
const BODY_PRESETS: { name: string; rules: Record<string, BodyPolicy> }[] = [
  { name: 'Minimal', rules: BODY_RULES_MINIMAL },
  { name: 'Recommended', rules: BODY_RULES_RECOMMENDED },
  { name: 'Everything', rules: BODY_RULES_EVERYTHING },
]
const BODY_POLICY_TABLE: { policy: BodyPolicy; kept: string; bytes: string }[] = [
  { policy: 'auto', kept: 'size-based: small text → DB, else disk', bytes: 'kept' },
  { policy: 'inline', kept: 'forced into the database column', bytes: 'kept' },
  { policy: 'disk', kept: 'full bytes offloaded to /bodies/<sha>', bytes: 'kept' },
  { policy: 'meta', kept: 'only size + sha256', bytes: 'dropped' },
]
function effectiveBodyPolicy(rules: Record<string, string> | undefined, fam: BodyFamily): string {
  return rules?.[fam] ?? BODY_RULES_RECOMMENDED[fam]
}
function activeBodyPreset(rules: Record<string, string> | undefined): string | null {
  const hit = BODY_PRESETS.find((p) =>
    BODY_FAMILIES.every((f) => effectiveBodyPolicy(rules, f.key) === p.rules[f.key]))
  return hit ? hit.name : null
}

// ── Capture settings slice (only what this modal owns).
interface CaptureSettings {
  captureProxyEnabled: boolean
  captureProxyPort: number
  captureProxyScope: string
  captureProxyStoreBodies: boolean
  captureProxyMaxBodyKb: number
  captureProxyMaxStoreMb: number
  captureProxyRetentionDays: number
  captureProxyRedactSecrets: boolean
  captureProxyPassiveDetect: boolean
  captureProxyStoreReqBodies: boolean
  captureProxyStoreRespBodies: boolean
  captureProxyBodyRules: Record<string, string>
  captureEgressBlockEmptyHost: boolean
  captureEgressBlockHardGuardrail: boolean
  captureEgressFailClosed: boolean
  captureEgressBlockUnresolvable: boolean
  captureEgressBlockPrivate: boolean
  captureEgressBlockLoopback: boolean
  captureEgressBlockLinkLocal: boolean
  captureEgressBlockCgnat: boolean
  captureEgressBlockReserved: boolean
  captureEgressBlockMulticast: boolean
  captureEgressBlockUnspecified: boolean
}

const DEFAULT_CAPTURE: CaptureSettings = {
  captureProxyEnabled: true, captureProxyPort: 8888, captureProxyScope: 'both',
  captureProxyStoreBodies: true, captureProxyMaxBodyKb: 64, captureProxyMaxStoreMb: 5,
  captureProxyRetentionDays: 14, captureProxyRedactSecrets: true, captureProxyPassiveDetect: true,
  captureProxyStoreReqBodies: true, captureProxyStoreRespBodies: true, captureProxyBodyRules: {},
  captureEgressBlockEmptyHost: true, captureEgressBlockHardGuardrail: true, captureEgressFailClosed: true,
  captureEgressBlockUnresolvable: true, captureEgressBlockPrivate: true, captureEgressBlockLoopback: true,
  captureEgressBlockLinkLocal: true, captureEgressBlockCgnat: true, captureEgressBlockReserved: true,
  captureEgressBlockMulticast: true, captureEgressBlockUnspecified: true,
}

function pickCapture(d: Record<string, unknown>): CaptureSettings {
  const b = (v: unknown, def: boolean) => (v == null ? def : Boolean(v))
  const n = (v: unknown, def: number) => (typeof v === 'number' ? v : def)
  return {
    captureProxyEnabled: b(d.captureProxyEnabled, true),
    captureProxyPort: n(d.captureProxyPort, 8888),
    captureProxyScope: (d.captureProxyScope as string) || 'both',
    captureProxyStoreBodies: b(d.captureProxyStoreBodies, true),
    captureProxyMaxBodyKb: n(d.captureProxyMaxBodyKb, 64),
    captureProxyMaxStoreMb: n(d.captureProxyMaxStoreMb, 5),
    captureProxyRetentionDays: n(d.captureProxyRetentionDays, 14),
    captureProxyRedactSecrets: b(d.captureProxyRedactSecrets, true),
    captureProxyPassiveDetect: b(d.captureProxyPassiveDetect, true),
    captureProxyStoreReqBodies: b(d.captureProxyStoreReqBodies, true),
    captureProxyStoreRespBodies: b(d.captureProxyStoreRespBodies, true),
    captureProxyBodyRules: (d.captureProxyBodyRules as Record<string, string>) ?? {},
    captureEgressBlockEmptyHost: b(d.captureEgressBlockEmptyHost, true),
    captureEgressBlockHardGuardrail: b(d.captureEgressBlockHardGuardrail, true),
    captureEgressFailClosed: b(d.captureEgressFailClosed, true),
    captureEgressBlockUnresolvable: b(d.captureEgressBlockUnresolvable, true),
    captureEgressBlockPrivate: b(d.captureEgressBlockPrivate, true),
    captureEgressBlockLoopback: b(d.captureEgressBlockLoopback, true),
    captureEgressBlockLinkLocal: b(d.captureEgressBlockLinkLocal, true),
    captureEgressBlockCgnat: b(d.captureEgressBlockCgnat, true),
    captureEgressBlockReserved: b(d.captureEgressBlockReserved, true),
    captureEgressBlockMulticast: b(d.captureEgressBlockMulticast, true),
    captureEgressBlockUnspecified: b(d.captureEgressBlockUnspecified, true),
  }
}

const label13 = { display: 'flex', flexDirection: 'column' as const, gap: 4, color: 'var(--text-secondary)', fontSize: 13 }
const row13 = { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, color: 'var(--text-secondary)', fontSize: 13 }

export function TrafficMindSettingsModal({ isOpen, onClose, userId }: {
  isOpen: boolean; onClose: () => void; userId: string | null
}) {
  const toast = useToast()
  const [s, setS] = useState<CaptureSettings>(DEFAULT_CAPTURE)
  // Last server-synced truth, per field. Used to (a) skip no-op saves and (b)
  // revert only the changed field on failure without clobbering other edits.
  const saved = useRef<CaptureSettings>({ ...DEFAULT_CAPTURE })

  const load = useCallback(async () => {
    if (!userId) return
    try {
      const resp = await fetch(`/api/users/${userId}/settings`)
      if (resp.ok) { const c = pickCapture(await resp.json()); saved.current = c; setS(c) }
    } catch { /* keep defaults */ }
  }, [userId])

  useEffect(() => { if (isOpen) load() }, [isOpen, load])

  // Every control persists immediately (like the egress toggles): PUT just the
  // changed field(s). On success adopt the server's canonical value for ONLY
  // those fields (so a concurrent in-flight change to another field is never
  // clobbered by this response); on failure revert ONLY those fields. Admin-only
  // server side, so a non-admin write reverts.
  const persist = useCallback(async (patch: Partial<CaptureSettings>) => {
    if (!userId) return
    const keys = Object.keys(patch) as (keyof CaptureSettings)[]
    try {
      const resp = await fetch(`/api/users/${userId}/settings`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch),
      })
      if (!resp.ok) throw new Error('save failed')
      const c = pickCapture(await resp.json())
      const next = { ...saved.current }
      for (const k of keys) (next[k] as CaptureSettings[typeof k]) = c[k]
      saved.current = next
      setS((prev) => { const n = { ...prev }; for (const k of keys) (n[k] as CaptureSettings[typeof k]) = c[k]; return n })
    } catch {
      toast.error('Not saved — TrafficMind settings are admin-only')
      setS((prev) => { const n = { ...prev }; for (const k of keys) (n[k] as CaptureSettings[typeof k]) = saved.current[k]; return n })
    }
  }, [userId, toast])

  // Discrete controls (toggles / selects / presets): optimistic update + persist now.
  const apply = useCallback(<K extends keyof CaptureSettings>(field: K, value: CaptureSettings[K]) => {
    setS((prev) => ({ ...prev, [field]: value }))
    persist({ [field]: value } as Partial<CaptureSettings>)
  }, [persist])

  // Number inputs: edit locally on change, persist on blur — but only if the
  // committed value actually differs from server truth, so a mere focus+blur
  // (no edit) never triggers a proxy respawn.
  const setLocal = useCallback(<K extends keyof CaptureSettings>(field: K, value: CaptureSettings[K]) => {
    setS((prev) => ({ ...prev, [field]: value }))
  }, [])
  const commit = <K extends keyof CaptureSettings>(field: K) => {
    if (s[field] !== saved.current[field]) persist({ [field]: s[field] } as Partial<CaptureSettings>)
  }

  return (
    <Modal isOpen={isOpen} onClose={onClose} size="large" title="TrafficMind settings">
      <div style={{ display: 'flex', flexDirection: 'column', maxHeight: '75vh' }}>
        <div style={{ overflowY: 'auto', paddingRight: 6, flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
            <div>
              <p style={{ margin: '0 0 6px', color: 'var(--text-tertiary)', fontSize: 'var(--text-sm, 13px)' }}>
                Master switch for the TrafficMind capture proxy. Enabling starts the proxy + ingest containers;
                disabling stops them. Turn capture on/off per project in the matrix below, or from the toggle on
                the TrafficMind page. <WikiInfoButton target="https://github.com/samugit83/redamon/wiki/TrafficMind" title="Open TrafficMind wiki page" />
              </p>
              <p style={{ margin: '0', color: 'var(--text-tertiary)', fontSize: 'var(--text-sm, 13px)' }}>
                There is a single
                shared capture proxy, so the master switch, container config, and egress guard apply to{' '}
                <strong>every user and every project</strong>. A change here is propagated to all of them, and the
                last save wins. Only administrators can edit them. (The one exception is the per-project routing
                matrix at the bottom, which is per-project.)
              </p>
            </div>
            <Toggle checked={s.captureProxyEnabled}
              onChange={(v) => apply('captureProxyEnabled', v)} aria-label="Enable TrafficMind capture" />
          </div>

          {s.captureProxyEnabled && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 16, marginTop: 18 }}>
              <label style={label13}>
                Listen port
                <input type="number" className="textInput" value={s.captureProxyPort}
                  onChange={(e) => setLocal('captureProxyPort', parseInt(e.target.value) || 8888)}
                  onBlur={() => commit('captureProxyPort')} />
              </label>
              <label style={label13}>
                Scope
                <select className="select" value={s.captureProxyScope} onChange={(e) => apply('captureProxyScope', e.target.value)}>
                  <option value="both">Recon + Agent</option>
                  <option value="recon">Recon only</option>
                  <option value="agent">Agent only</option>
                </select>
              </label>
              <label style={label13}>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  Inline body cap (KB)
                  <Tooltip content="Text bodies at or under this size are stored INLINE in the database (fast, shown directly in the UI). Larger text and all binary go to disk. This is a DB-vs-disk routing threshold, not a size limit — nothing is dropped by it." position="top" maxWidth={460}>
                    <Info size={13} style={{ color: 'var(--text-tertiary)', cursor: 'help' }} />
                  </Tooltip>
                </span>
                <input type="number" className="textInput" value={s.captureProxyMaxBodyKb}
                  onChange={(e) => setLocal('captureProxyMaxBodyKb', parseInt(e.target.value) || 64)}
                  onBlur={() => commit('captureProxyMaxBodyKb')} />
              </label>
              <label style={label13}>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  Max store (MB)
                  <Tooltip content="Hard ceiling: any body larger than this is NOT stored (only its size + sha256 are kept), regardless of family policy. 0 = unlimited. This is the real size cap that keeps multi-MB blobs off disk." position="top" maxWidth={460}>
                    <Info size={13} style={{ color: 'var(--text-tertiary)', cursor: 'help' }} />
                  </Tooltip>
                </span>
                <input type="number" className="textInput" value={s.captureProxyMaxStoreMb}
                  onChange={(e) => setLocal('captureProxyMaxStoreMb', Math.max(0, parseInt(e.target.value) || 0))}
                  onBlur={() => commit('captureProxyMaxStoreMb')} />
              </label>
              <label style={label13}>
                Retention (days)
                <input type="number" className="textInput" value={s.captureProxyRetentionDays}
                  onChange={(e) => setLocal('captureProxyRetentionDays', parseInt(e.target.value) || 14)}
                  onBlur={() => commit('captureProxyRetentionDays')} />
              </label>
              <div style={row13}>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  Store bodies
                  <Tooltip content="Master switch. Off = keep only metadata (size + sha256) for every body. On = keep bodies per the direction toggles and family policy below." position="top" maxWidth={420}>
                    <Info size={13} style={{ color: 'var(--text-tertiary)', cursor: 'help' }} />
                  </Tooltip>
                </span>
                <Toggle checked={s.captureProxyStoreBodies} onChange={(v) => apply('captureProxyStoreBodies', v)} aria-label="Store bodies" />
              </div>
              <div style={row13}>
                <span>Store request bodies</span>
                <Toggle checked={s.captureProxyStoreReqBodies} onChange={(v) => apply('captureProxyStoreReqBodies', v)} aria-label="Store request bodies" />
              </div>
              <div style={row13}>
                <span>Store response bodies</span>
                <Toggle checked={s.captureProxyStoreRespBodies} onChange={(v) => apply('captureProxyStoreRespBodies', v)} aria-label="Store response bodies" />
              </div>
              <div style={row13}>
                <span>Redact secrets</span>
                <Toggle checked={s.captureProxyRedactSecrets} onChange={(v) => apply('captureProxyRedactSecrets', v)} aria-label="Redact secrets" />
              </div>
              <div style={row13}>
                <span>Passive detections</span>
                <Toggle checked={s.captureProxyPassiveDetect} onChange={(v) => apply('captureProxyPassiveDetect', v)} aria-label="Passive detections" />
              </div>
            </div>
          )}

          {s.captureProxyEnabled && (
            <div style={{ marginTop: 18, borderTop: '1px solid var(--border-default)', paddingTop: 16 }}>
              <h4 style={{ margin: '0 0 4px', color: 'var(--text-primary)', fontSize: 'var(--text-md, 14px)', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                Body storage
                <Tooltip position="top" maxWidth={460} content={
                  <div style={{ fontSize: 12, lineHeight: 1.45, textAlign: 'left' }}>
                    <div style={{ marginBottom: 6 }}>Per content-type family, choose where each captured body goes:</div>
                    <table style={{ borderCollapse: 'collapse', width: '100%' }}>
                      <thead>
                        <tr>
                          <th style={{ textAlign: 'left', padding: '2px 8px 4px 0', borderBottom: '1px solid var(--border-default)' }}>Policy</th>
                          <th style={{ textAlign: 'left', padding: '2px 8px 4px 0', borderBottom: '1px solid var(--border-default)' }}>What&apos;s kept</th>
                          <th style={{ textAlign: 'left', padding: '2px 0 4px 0', borderBottom: '1px solid var(--border-default)' }}>Bytes</th>
                        </tr>
                      </thead>
                      <tbody>
                        {BODY_POLICY_TABLE.map((r) => (
                          <tr key={r.policy}>
                            <td style={{ padding: '3px 8px 3px 0', verticalAlign: 'top' }}><code>{r.policy}</code></td>
                            <td style={{ padding: '3px 8px 3px 0', verticalAlign: 'top' }}>{r.kept}</td>
                            <td style={{ padding: '3px 0', verticalAlign: 'top', color: r.bytes === 'dropped' ? 'var(--danger, #e5484d)' : 'var(--text-secondary)' }}>{r.bytes}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <div style={{ marginTop: 6 }}>The <strong>Max store (MB)</strong> ceiling overrides disk/inline for oversized bodies.</div>
                  </div>
                }>
                  <Info size={14} style={{ color: 'var(--text-tertiary)', cursor: 'help' }} />
                </Tooltip>
              </h4>
              <p style={{ margin: '0 0 12px', color: 'var(--text-tertiary)', fontSize: 'var(--text-sm, 13px)' }}>
                Where each body is kept. <strong>Recommended</strong> keeps text/JSON, drops media noise (images, fonts, video), and offloads leak-worthy downloads (documents, archives, binaries) to disk.
              </p>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
                <span style={{ color: 'var(--text-tertiary)', fontSize: 12 }}>Preset</span>
                <div className={styles.segmentedControl} role="tablist" aria-label="Body storage preset">
                  {BODY_PRESETS.map((preset) => {
                    const active = activeBodyPreset(s.captureProxyBodyRules) === preset.name
                    return (
                      <button key={preset.name} type="button" role="tab" aria-selected={active}
                        className={`${styles.segmentedOption} ${active ? styles.segmentedOptionActive : ''}`}
                        onClick={() => { if (activeBodyPreset(s.captureProxyBodyRules) !== preset.name) apply('captureProxyBodyRules', { ...preset.rules }) }}>
                        {preset.name}
                      </button>
                    )
                  })}
                </div>
                {activeBodyPreset(s.captureProxyBodyRules) === null && (
                  <span style={{ color: 'var(--text-tertiary)', fontSize: 12, fontStyle: 'italic' }}>Custom</span>
                )}
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 12 }}>
                {BODY_FAMILIES.map((fam) => {
                  const current = s.captureProxyBodyRules?.[fam.key] ?? BODY_RULES_RECOMMENDED[fam.key]
                  return (
                    <div key={fam.key} style={row13}>
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                        <span>{fam.title}</span>
                        <Tooltip content={fam.tip} position="top" maxWidth={420}>
                          <Info size={13} style={{ color: 'var(--text-tertiary)', cursor: 'help' }} />
                        </Tooltip>
                      </span>
                      <select className="select" style={{ maxWidth: 110 }} value={current}
                        onChange={(e) => apply('captureProxyBodyRules', { ...s.captureProxyBodyRules, [fam.key]: e.target.value })}
                        aria-label={`${fam.title} storage policy`}>
                        {BODY_POLICIES.map((p) => <option key={p} value={p}>{p}</option>)}
                      </select>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {s.captureProxyEnabled && (
            <div style={{ marginTop: 18, borderTop: '1px solid var(--border-default)', paddingTop: 16 }}>
              <h4 style={{ margin: '0 0 4px', color: 'var(--text-primary)', fontSize: 'var(--text-md, 14px)', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                Egress guard
                <Tooltip content="The egress guard stops the capture proxy becoming an SSRF pivot into RedAmon's internal network. Each condition below is REFUSED (returns 403, nothing forwarded). All default to block; relaxing one lets the proxy reach that class of destination. RedAmon's own service IPs stay blocked via the separate blocked-IPs denylist regardless." position="top" maxWidth={520}>
                  <Info size={14} style={{ color: 'var(--text-tertiary)', cursor: 'help' }} />
                </Tooltip>
              </h4>
              <p style={{ margin: '0 0 12px', color: 'var(--text-tertiary)', fontSize: 'var(--text-sm, 13px)' }}>
                Which destinations the proxy refuses. Every toggle is <strong>block</strong> by default; turn one off only to deliberately allow that class (e.g. private IPs to reach an internal / lab target on a private Docker network). These persist immediately.
              </p>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 12 }}>
                {EGRESS_TOGGLES.map((t) => (
                  <div key={t.key} style={row13}>
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                      <span>{t.title}</span>
                      {t.danger && <span title="Relaxing this has a real security cost" style={{ color: 'var(--danger, #e5484d)', fontSize: 12, lineHeight: 1 }}>⚠</span>}
                      <Tooltip content={t.tip} position="top" maxWidth={480}>
                        <Info size={13} style={{ color: 'var(--text-tertiary)', cursor: 'help' }} />
                      </Tooltip>
                    </span>
                    <Toggle checked={s[t.key]} onChange={(v) => apply(t.key, v)} aria-label={t.title} />
                  </div>
                ))}
              </div>
            </div>
          )}

          {s.captureProxyEnabled && <TrafficMindProjectMatrix />}
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 12, borderTop: '1px solid var(--border-default)', paddingTop: 14, marginTop: 6 }}>
          <span style={{ color: 'var(--text-tertiary)', fontSize: 12, marginRight: 'auto' }}>Every change saves automatically.</span>
          <button className="secondaryButton" onClick={onClose} type="button">Close</button>
        </div>
      </div>
    </Modal>
  )
}

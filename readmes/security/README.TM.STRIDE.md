# RedAmon Threat Model

> **Continuation of** [`README.TM.SYSTEM_OVERVIEW.md`](./README.TM.SYSTEM_OVERVIEW.md). That document established the system overview, assets, architecture, data flow, trust boundaries, and entry points (Sections 1–5). This document begins the **threat enumeration** phase.
>
> **Scope of this document:** Threat **discovery, classification, and evaluation** using the STRIDE model. Mitigation and control design are **out of scope** for this stage and are deferred to the next phase.
>
> **Deployment assumption (carried over):** RedAmon runs **locally** (single host, Docker Compose), is **not** intended for public-internet exposure, and host-published ports are reachable on the operator's machine/LAN. Likelihood ratings below are calibrated against this assumption (the realistic adversary is a LAN-resident host, a compromised target-facing worker/scanner, a malicious LLM/tool output, or a co-resident container — **not** an anonymous internet attacker).

**Project version:** 5.1.0 (`VERSION`)

## Threat Analysis (STRIDE)

### Table of Contents

1. [Methodology](#methodology)
2. [Threat Overview](#threat-overview)
3. [STRIDE Analysis by Component](#stride-analysis-by-component)
   - [S – Spoofing](#s--spoofing) *(analysed this session)*
   - [T – Tampering](#t--tampering) *(pending)*
   - [R – Repudiation](#r--repudiation) *(pending)*
   - [I – Information Disclosure](#i--information-disclosure) *(pending)*
   - [D – Denial of Service](#d--denial-of-service) *(pending)*
   - [E – Elevation of Privilege](#e--elevation-of-privilege) *(pending)*
4. [Threat Prioritization](#threat-prioritization)

---

## Methodology

This analysis applies the **STRIDE** model across every component, trust boundary, and asset identified in Sections 1–5 of the System Overview. Each component reachable across a trust boundary (browser → webapp, webapp → orchestrator, webapp/agent → service APIs, orchestrator → Docker daemon, worker → graph, platform → targets/LLMs, and the DB tier) was examined against the six STRIDE dimensions, using **only repository evidence**: application source (`webapp/src`, `agentic/`, `recon_orchestrator/`, `docker_broker/`, `mcp/servers/`), `docker-compose.yml`, the committed `.env`, Dockerfiles, and the Prisma/Neo4j schemas.

| STRIDE                         | Description                                              |
| :----------------------------- | :------------------------------------------------------- |
| **S – Spoofing**               | Identity falsification, authentication bypass            |
| **T – Tampering**              | Unauthorized alteration of data, code, or configurations |
| **R – Repudiation**            | Missing or weak audit and traceability controls          |
| **I – Information Disclosure** | Exposure of sensitive or confidential data               |
| **D – Denial of Service**      | Resource exhaustion, availability impact                 |
| **E – Elevation of Privilege** | Unauthorized access to higher privileges or roles        |

Each STRIDE dimension is evaluated against the architectural findings from Sections 1–5. Threats are assigned a stable **ID** (`<category-letter><n>`, restarting per category), a **Likelihood** and **Impact** (Low/Med/High), a derived **Risk Level** (Low/Medium/High/Critical), and an **Evidence** pointer to the exact file and line range. The same IDs are reused verbatim in the [Threat Prioritization](#threat-prioritization) table.

**Risk derivation** (likelihood × impact):

| | Impact Low | Impact Med | Impact High |
| :--- | :--- | :--- | :--- |
| **Likelihood High** | Medium | High | Critical |
| **Likelihood Med** | Low | Medium | High |
| **Likelihood Low** | Low | Low | Medium |

> **Status of this session:** Deep, evidence-based analysis has been completed for **S – Spoofing** only. The Tampering, Repudiation, Information Disclosure, Denial of Service, and Elevation of Privilege sections contain the full heading structure with `[PENDING: Analysis to be performed in next session]` placeholders and will be completed in subsequent sessions.

---

## Threat Overview

The dominant theme of RedAmon's **spoofing** landscape is that the platform's security model relies on a small set of **shared static service keys** and on **identity claims carried in request bodies / WebSocket init frames that are never cryptographically bound to an authenticated session**. The webapp front door is well-built (HS256 JWT in an httpOnly cookie, bcrypt-12, constant-time orchestrator-key check), but behind it the internal trust mesh is largely flat: whoever holds `INTERNAL_API_KEY` is treated as the trusted control plane, and that key is deliberately handed to the **least-trusted, target-facing scanner containers**.

Most exposed layers and weak data flows identified:

* **Weak fallback credentials in `docker-compose.yml`** — `AUTH_SECRET`, `INTERNAL_API_KEY`, `ORCHESTRATOR_API_KEY` default to `changeme`; `NEO4J_PASSWORD` to `changeme123`; `POSTGRES_PASSWORD` to `redamon_secret`; `GVM_PASSWORD` to `admin`. The committed `.env` supplies strong random values for the first three, but **does not set the Neo4j/Postgres/GVM passwords**, so those weak defaults are live unless the operator overrides them.
* **Internal-key auth bypass** — any request bearing the correct `x-internal-key` skips JWT entirely (`middleware.ts:45-49`) and is *not* re-stamped with a verified `x-user-id`, so it may carry attacker-chosen identity headers.
* **Identity-from-body** — the agent's `/graph/exec`, `/guardrail/check-target`, `/ws/agent` init frame, and the Kali terminal init frame all take `user_id`/`project_id` from the caller with no authentication.
* **Unauthenticated MCP offensive tooling** — the five MCP servers (ports 8000–8005) bind `0.0.0.0` with no auth; any LAN host can impersonate the agent and drive nmap/nuclei/metasploit/hydra/playwright.
* **Spoofable worker provisioning** — the unauthenticated tunnel-config sync flow lets any internal caller masquerade as a booting worker.
* **LAN-exposed datastores** — PostgreSQL (5432), Neo4j (7474/7687), and the MCP ports publish on `0.0.0.0`, turning every default credential and missing-auth listener into a LAN-reachable impersonation vector.

The net effect: an adversary who reaches the host's loopback/LAN, or who compromises a single target-facing scanner or a tool/LLM output, can impersonate higher-trust principals (operator, control plane, tenant) across nearly every internal boundary.

---

## STRIDE Analysis by Component

The Spoofing analysis below is presented as a single consolidated table because spoofing threats in RedAmon span multiple components; the **Asset / Entry Point** and **Evidence / Notes** columns identify the specific component and file for each threat. The remaining STRIDE categories follow with their structure in place and analysis pending.

### S – Spoofing

Identity falsification and authentication bypass. **14 distinct, evidence-based threats** (S1–S14).

| ID | STRIDE Category | Threat Description | Asset / Entry Point | Likelihood | Impact | Risk Level | Evidence / Notes |
| :-- | :-- | :-- | :-- | :-- | :-- | :-- | :-- |
| **S1** | S – Spoofing | **Forgeable JWT session tokens via weak default `AUTH_SECRET`.** The webapp signs auth cookies with HS256 using `AUTH_SECRET`. In `docker-compose.yml` this falls back to the literal `changeme` when the env var is unset, and `middleware.ts` treats a `changeme` (or missing) secret by returning `null` from `getSecret()`, which simply rejects tokens rather than failing the boot loudly. Because HS256 is symmetric, anyone who knows the signing key can mint a valid token for *any* `sub`/`role`. **Concrete attack:** An operator deploys without exporting `AUTH_SECRET` (or copies a tutorial `.env`). The attacker, knowing RedAmon's published default, runs a three-line `jose`/`pyjwt` script to sign `{"sub":"<admin-user-id>","role":"admin"}` with key `changeme`, sets it as the `redamon-auth` cookie, and is now an authenticated admin: full access to projects, RoE PII, the graph, and the unmasked LLM/OSINT keys. The committed `.env` *does* set a strong random `AUTH_SECRET` (line 1), so the threat is realized only on deployments that drop or fail to supply it — but the weak fallback is shipped in compose. | Web auth / `redamon-auth` cookie; all webapp routes | Med | High | High | `webapp/src/lib/auth.ts` (HS256 signing); `webapp/src/middleware.ts:8-24`; `docker-compose.yml:670` (`AUTH_SECRET:-changeme`); `.env:1` sets strong value |
| **S2** | S – Spoofing | **`INTERNAL_API_KEY` is a shared static bearer that bypasses all user authentication.** `middleware.ts:45-49` returns `NextResponse.next()` for any request whose `x-internal-key` header equals `INTERNAL_API_KEY` (and is not `changeme`), skipping JWT verification entirely. The same single key authenticates every internal caller; there is no per-principal identity, no rotation, and no audience binding. **Concrete attack:** An attacker who obtains the key from any one holder — a leaked env, a scanner container (see S3), an agent log line, or a `changeme` default — sends `GET /api/users/<victim>/llm-providers?internal=true` with header `x-internal-key: <key>` and receives the victim's **unmasked** OpenAI/Anthropic/AWS keys. The webapp cannot distinguish this forged caller from the legitimate agent, because possession of the key *is* the identity. This collapses the carefully drawn trust boundary between the control plane and everything that merely *holds* the key into a single point of impersonation. | webapp internal API surface; `x-internal-key` header | Med | High | High | `webapp/src/middleware.ts:45-49`; `webapp/src/lib/session.ts` (`isInternalRequest`, non-constant-time `===`); single shared key |
| **S3** | S – Spoofing | **Target-facing scanner containers hold `INTERNAL_API_KEY`, letting a compromised worker impersonate the control plane.** `recon_orchestrator/container_manager.py` injects `INTERNAL_API_KEY` into the environment of every spawned recon/GVM/secret-hunt container. These are the *least-trusted* components in the Section-4 trust model — they touch attacker-controlled targets and run offensive tools that parse hostile output. **Concrete attack:** A target serves a crafted response that triggers RCE in a scanning tool (or the operator runs a trojaned Nuclei template/wordlist). The attacker now executes inside a scanner that already exports `INTERNAL_API_KEY`. They read the env var and call back to the webapp (`x-internal-key`) to harvest every user's LLM/OSINT keys (S2), or call the orchestrator's RoE/guardrail endpoints to mark out-of-scope targets as approved. The worker, designed to "hold no secrets," in practice carries the master internal credential off the trust island. | Spawned scanners (pentest-net / host net); env `INTERNAL_API_KEY` | Med | High | High | `recon_orchestrator/container_manager.py:~276` (`"INTERNAL_API_KEY": os.environ.get(...)`); contradicts "worker holds no secrets" intent in Section 4 |
| **S4** | S – Spoofing | **Identity headers (`x-user-id`/`x-user-role`) are not stripped on the internal-key path, allowing downstream identity forgery.** On the JWT path, `middleware.ts:72-74` overwrites `x-user-id`/`x-user-role` from the verified token. But the internal-key short-circuit at lines 45-49 returns *before* that block, so any inbound `x-user-id`/`x-user-role` headers pass through untouched. Downstream API routes that read these headers (the documented mechanism for user context) will trust whatever the caller sent. **Concrete attack:** A holder of `INTERNAL_API_KEY` (e.g., a compromised scanner, S3) sends a request with `x-internal-key: <key>`, `x-user-id: <any-admin-id>`, `x-user-role: admin` to a route that gates on `x-user-role`. The middleware waves it through without re-stamping, and the route acts as the spoofed admin. Even on the JWT path, the absence of an explicit delete-then-set (only `set`) means defensive coding is missing; the internal path makes it directly exploitable. | webapp downstream routes; injected identity headers | Med | High | High | `webapp/src/middleware.ts:45-49` (early return) vs `:71-74` (set only on JWT path); no inbound header stripping |
| **S5** | S – Spoofing | **`/graph/exec` derives tenant identity from the request body with no authentication.** The agent's `POST /graph/exec` accepts `user_id` and `project_id` in the JSON body and injects them as Neo4j tenant filters; it validates only that they are non-empty and that the Cypher matches a labelled-node pattern. There is no JWT, cookie, or `x-internal-key` check binding the caller to the claimed tenant. **Concrete attack:** Any host that can reach the agent on `:8090` (LAN, per Section 5) sends `{"user_id":"<victim>","project_id":"<victim-project>","cypher":"MATCH (n:Host) RETURN n"}`. The endpoint dutifully injects the victim's tenant filter and returns that tenant's recon findings, discovered secrets, and attack chains. Because `project_id`/`user_id` are guessable or enumerable, an attacker reads across tenants at will. The tenant filter enforces *scoping* but never *authentication* — it assumes the caller is honest about who they are. | agent `POST /graph/exec`; Neo4j tenant data | High | High | Critical | `agentic/api.py:~2724-2748` (body `user_id`/`project_id`); `graph_db/tenant_filter.py` (`inject_tenant_filter`); unauthenticated endpoint on LAN-exposed `:8090` |
| **S6** | S – Spoofing | **`WS /ws/agent` session init accepts unauthenticated `user_id`/`project_id`/`session_id`.** The agent WebSocket `handle_init` constructs an `InitMessage` straight from the client-supplied payload and calls `ws_manager.authenticate()` with those raw values — there is no token check; the method name "authenticate" is a misnomer for "record the claimed identity." **Concrete attack:** An attacker opens a WebSocket to `ws://host:8090/ws/agent` and sends `{"type":"init","user_id":"<victim>","project_id":"<victim-project>","session_id":"<guess>"}`. They are now bound to the victim's agent session: they can stream the victim's agent reasoning, tool outputs, and exploitation artifacts, and issue commands that run under the victim's project context and per-user LLM keys. Identity is entirely self-asserted over an unauthenticated channel reachable on the LAN. | agent `WS /ws/agent`; agent session state | High | High | Critical | `agentic/websocket_api.py:~99-105, ~940-951` (`InitMessage` → `authenticate`); no JWT/cookie validation |
| **S7** | S – Spoofing | **Predictable, low-entropy `session_id` enables session-identity guessing.** The webapp generates session IDs client-side as `'session_' + 8 chars` drawn from a 36-symbol alphabet using `Math.random()` (`useSession.ts`). `Math.random()` is not a CSPRNG and the keyspace is only 36^8. Combined with S6's unauthenticated init, a guessed `session_id` paired with a known/enumerated `user_id` lets an attacker rejoin or collide with a live session. **Concrete attack:** The attacker enumerates plausible `user_id`s (or learns one from S5), then sweeps `/ws/agent` init frames with candidate `session_id`s; on a hit they attach to an in-flight engagement, observing live tool output and injecting messages as the operator. Even without brute force, `Math.random()` streams can be partially predicted from prior outputs. The weak generator turns "know the session ID" from a secret into a guessable token. | Session identifier; `WS /ws/agent` | Med | Med | Medium | `webapp/src/hooks/useSession.ts:7-12` (`Math.random()`, 8 chars); amplifies S6 |
| **S8** | S – Spoofing | **`WS /ws/kali-terminal` proxy accepts connections with no authentication.** The agent exposes a WebSocket that `accept()`s immediately and bridges to the Kali sandbox terminal, with no init-frame auth, JWT, or key check before forwarding. **Concrete attack:** A LAN host (or any process that can reach `:8090`) connects to `ws://host:8090/ws/kali-terminal` and is dropped straight into an interactive PTY inside the Kali sandbox — a container holding `NET_RAW`/`NET_ADMIN` and `seccomp:unconfined`. The attacker now runs arbitrary commands in the offensive-tool runtime, impersonating the operator's terminal session, with no record tying the session to an identity. This is full operator impersonation at the most powerful runtime in the system. While the underlying Kali terminal server (`:8016`) is loopback-bound, the *agent proxy* re-exposes it on the LAN-reachable agent port without adding auth. | agent `WS /ws/kali-terminal` → Kali PTY | Med | High | High | `agentic/api.py:~2772-2828` (`await websocket.accept()` then proxy, no auth) |
| **S9** | S – Spoofing | **Kali terminal init frame carries unsigned tenant context.** The terminal server reads an optional `init` frame of shape `{"type":"init","user_id":...,"project_id":...}` and exports the supplied values as `REDAMON_USER_ID`/`REDAMON_PROJECT_ID` into the spawned shell, with no signature or verification. Although the listener is bound to `127.0.0.1:8016`, S8's agent proxy and any local process can drive it. **Concrete attack:** A local attacker (or the unauthenticated proxy path of S8) sends an init frame claiming `user_id=<admin>`, `project_id=<prod>`. The PTY environment is now stamped with the spoofed tenant, so any workspace reads/writes or tenant-scoped tooling executed in that shell act as the victim tenant. The tenant context that downstream tooling trusts is fully attacker-controlled and bound to no authenticated principal. | Kali terminal init frame; PTY tenant env | Med | Med | Medium | `mcp/servers/terminal_server.py:~51-90` (`_read_init_frame`, no HMAC/verification) |
| **S10** | S – Spoofing | **Unauthenticated MCP servers (8000–8005) let any LAN host impersonate the agent and drive offensive tooling.** The five FastMCP/SSE servers (network-recon, nuclei, metasploit, nmap, playwright) bind `MCP_HOST=0.0.0.0` and run `mcp.run(transport="sse", ...)` with no bearer token, header check, or client identity — `mcp_registry` supports optional `BearerAuth` but the system servers ship with `auth=None`. **Concrete attack:** A host on the operator's LAN connects to `http://<host>:8000/sse` and invokes `kali_shell`, `execute_hydra`, `execute_curl` (SSRF), or to `:8003` and fires Metasploit modules — all attributed to nobody, all running with the sandbox's raw network capabilities. The attacker has effectively assumed the agent's identity as the sole legitimate MCP client, weaponizing the operator's own offensive infrastructure against arbitrary targets without ever touching the authenticated webapp. | Kali MCP servers `:8000-8005` (SSE) | Med | High | High | `docker-compose.yml:430-434, 461` (`MCP_HOST: 0.0.0.0`); `mcp/servers/network_recon_server.py:~91-92,342`; `agentic/mcp_registry.py` (`auth: Optional[BearerAuth] = None`) |
| **S11** | S – Spoofing | **`/guardrail/check-target` impersonates a user to fetch their LLM keys.** The agent's `POST /guardrail/check-target` accepts `user_id`/`project_id` in the body and, when `user_id` is present, calls the webapp `GET /api/users/<user_id>/llm-providers?internal=true` using `INTERNAL_API_KEY` — i.e., it acts on behalf of whatever user the *caller* names, with no proof the caller is that user. **Concrete attack:** An attacker reaching the agent posts `{"user_id":"<victim>","target":"x"}`. The agent obligingly fetches the victim's unmasked provider configuration (used to pick a judge model), giving the attacker a primitive that exfiltrates or confirms another tenant's LLM credentials via the agent as a confused deputy. The endpoint trusts the body-supplied `user_id` as identity and lends it the agent's internal-key privilege. | agent `POST /guardrail/check-target`; per-user LLM keys | Med | High | High | `agentic/api.py:~145-150, 179-203` (`body.user_id` → `?internal=true` fetch with `X-Internal-Key`) |
| **S12** | S – Spoofing | **`ORCHESTRATOR_API_KEY` is a single shared static key with a weak default.** The webapp authenticates to the loopback-bound orchestrator with `X-Orchestrator-Key`; `orchestrator.ts` falls back to literal `changeme`, and `docker-compose.yml:357/675` defaults the same. The orchestrator validates it correctly with constant-time `hmac.compare_digest` (`recon_orchestrator/auth.py`), but a single guessable/leaked key is the entire control. **Concrete attack:** On a deployment that didn't set the key (default `changeme`), any process that can reach `127.0.0.1:8010` — a local user, a co-resident container with loopback access, or an SSRF primitive in another local service — sends `POST /recon/<project>/start` with `X-Orchestrator-Key: changeme` and launches arbitrary scans, spawning host-network containers via the broker. The committed `.env` (line 3) sets a strong value, so realistic exposure is on misconfigured deployments plus any path that leaks the static key. | webapp → orchestrator; `X-Orchestrator-Key` | Med | High | High | `webapp/src/lib/orchestrator.ts:14-22` (`|| 'changeme'`); `recon_orchestrator/auth.py` (constant-time); `docker-compose.yml:357,675`; `.env:3` strong |
| **S13** | S – Spoofing | **LAN-exposed datastores with default credentials enable principal impersonation.** `docker-compose.yml` defaults `NEO4J_PASSWORD` to `changeme123`, `POSTGRES_PASSWORD` to `redamon_secret`, and `GVM_PASSWORD` to `admin`, and publishes PostgreSQL (5432) and Neo4j (7474/7687) on `0.0.0.0`. Critically, the committed `.env` sets *none* of these three, so the weak defaults are **live** unless the operator overrides them. **Concrete attack:** A host on the LAN runs `cypher-shell -a bolt://<host>:7687 -u neo4j -p changeme123` and gains full read/write to every engagement's findings, secrets, and attack graph — impersonating the Neo4j principal that the agent and orchestrator trust. The same host connects `psql -h <host> -U redamon -p` (password `redamon_secret`) to read user rows, hashes, and stored keys, or authenticates to GVM as `admin/admin` to drive vulnerability scans. Default-credential reuse turns network reachability directly into authenticated-principal impersonation. | PostgreSQL `:5432`, Neo4j `:7474/7687`, GVM | Med | High | High | `docker-compose.yml:25` (`redamon_secret`), `:44/376/513/593/668` (`changeme123`), `:384` (`GVM_PASSWORD:-admin`); not set in `.env` |
| **S14** | S – Spoofing | **Unauthenticated tunnel-config sync lets any caller masquerade as a booting worker.** `/api/global/tunnel-config/sync` is on the webapp public-path allowlist (`middleware.ts:6`), so it requires no auth; when hit, the webapp reads tunnel secrets from the DB and POSTs them to `http://kali-sandbox:8015/tunnel/configure`, and the tunnel manager accepts that POST with no authentication. **Concrete attack:** Any container or LAN host that can reach the webapp triggers `POST /api/global/tunnel-config/sync`, spoofing the "worker just booted, push me the tunnel config" signal — causing the webapp to fan out tunnel credentials and (re)configure ngrok/chisel tunnels on the worker. Alternatively, an attacker reaching `:8015` directly POSTs a crafted `/tunnel/configure` body to point reverse-shell/C2 tunnels at an attacker-controlled server. Neither side authenticates the other, so a worker (and its tunnel egress) can be impersonated or hijacked. | `/api/global/tunnel-config/sync`; tunnel manager `:8015` | Med | Med | Medium | `webapp/src/middleware.ts:6` (PUBLIC_PATHS); `webapp/src/app/api/global/tunnel-config/sync/route.ts`; `mcp/servers/tunnel_manager.py:~144-161` (no auth on POST) |

> **Spoofing summary:** The cluster of **Critical** spoofing threats (S5, S6) stems from the agent service trusting body/init-frame identity claims on LAN-reachable ports. The **High** band (S1–S4, S8, S10–S13) is dominated by the shared-static-key model (`INTERNAL_API_KEY`/`ORCHESTRATOR_API_KEY`), weak compose defaults, and unauthenticated MCP/datastore exposure. S7, S9, and S14 are **Medium** — real but gated by loopback binding or by needing a preceding foothold.

---

### T – Tampering

Unauthorized alteration of data, code, or configurations.

| ID | STRIDE Category | Threat Description | Asset / Entry Point | Likelihood | Impact | Risk Level | Evidence / Notes |
| :-- | :-- | :-- | :-- | :-- | :-- | :-- | :-- |
| | T – Tampering | `[PENDING: Analysis to be performed in next session]` | | | | | |

---

### R – Repudiation

Missing or weak audit and traceability controls.

| ID | STRIDE Category | Threat Description | Asset / Entry Point | Likelihood | Impact | Risk Level | Evidence / Notes |
| :-- | :-- | :-- | :-- | :-- | :-- | :-- | :-- |
| | R – Repudiation | `[PENDING: Analysis to be performed in next session]` | | | | | |

---

### I – Information Disclosure

Exposure of sensitive or confidential data.

| ID | STRIDE Category | Threat Description | Asset / Entry Point | Likelihood | Impact | Risk Level | Evidence / Notes |
| :-- | :-- | :-- | :-- | :-- | :-- | :-- | :-- |
| | I – Information Disclosure | `[PENDING: Analysis to be performed in next session]` | | | | | |

---

### D – Denial of Service

Resource exhaustion, availability impact.

| ID | STRIDE Category | Threat Description | Asset / Entry Point | Likelihood | Impact | Risk Level | Evidence / Notes |
| :-- | :-- | :-- | :-- | :-- | :-- | :-- | :-- |
| | D – Denial of Service | `[PENDING: Analysis to be performed in next session]` | | | | | |

---

### E – Elevation of Privilege

Unauthorized access to higher privileges or roles.

| ID | STRIDE Category | Threat Description | Asset / Entry Point | Likelihood | Impact | Risk Level | Evidence / Notes |
| :-- | :-- | :-- | :-- | :-- | :-- | :-- | :-- |
| | E – Elevation of Privilege | `[PENDING: Analysis to be performed in next session]` | | | | | |

---

## Threat Prioritization

Consolidated, prioritized summary. This table currently contains only the **Spoofing** threats analysed this session (S1–S14); rows for T/R/I/D/E will be appended as each category is completed. Priority bands: **P1** = Critical/High requiring immediate attention, **P2** = Medium, **P3** = Low.

| Threat ID | Description | STRIDE Category | Component | Likelihood | Impact | Overall Risk | Priority |
| :-- | :-- | :-- | :-- | :-- | :-- | :-- | :-- |
| S5 | `/graph/exec` tenant identity taken from request body, unauthenticated → cross-tenant graph read | Spoofing | Agent (FastAPI) | High | High | **Critical** | **P1** |
| S6 | `WS /ws/agent` init frame self-asserts `user_id`/`project_id`/`session_id` → session hijack | Spoofing | Agent (WebSocket) | High | High | **Critical** | **P1** |
| S1 | Weak default `AUTH_SECRET` (`changeme`) enables HS256 JWT forgery | Spoofing | Web auth | Med | High | High | **P1** |
| S2 | Shared static `INTERNAL_API_KEY` bypasses all user auth in middleware | Spoofing | webapp middleware | Med | High | High | **P1** |
| S3 | Target-facing scanners hold `INTERNAL_API_KEY` → worker impersonates control plane | Spoofing | recon-orchestrator / scanners | Med | High | High | **P1** |
| S4 | `x-user-id`/`x-user-role` not stripped on internal-key path → downstream identity forgery | Spoofing | webapp middleware | Med | High | High | **P1** |
| S8 | `WS /ws/kali-terminal` proxy accepts connections with no auth → operator PTY impersonation | Spoofing | Agent → Kali sandbox | Med | High | High | **P1** |
| S10 | Unauthenticated MCP servers (8000–8005) → any LAN host drives offensive tooling | Spoofing | Kali MCP servers | Med | High | High | **P1** |
| S11 | `/guardrail/check-target` fetches LLM keys for body-supplied `user_id` (confused deputy) | Spoofing | Agent (FastAPI) | Med | High | High | **P1** |
| S12 | `ORCHESTRATOR_API_KEY` single shared static key with weak default | Spoofing | webapp → orchestrator | Med | High | High | **P1** |
| S13 | LAN-exposed Neo4j/PostgreSQL/GVM with default creds (not set in `.env`) | Spoofing | Datastores / GVM | Med | High | High | **P1** |
| S7 | Predictable low-entropy `session_id` (`Math.random()`, 36^8) amplifies S6 | Spoofing | webapp session | Med | Med | Medium | **P2** |
| S9 | Kali terminal init frame carries unsigned tenant context | Spoofing | Kali terminal server | Med | Med | Medium | **P2** |
| S14 | Unauthenticated tunnel-config sync → spoof a booting worker / hijack tunnels | Spoofing | webapp / tunnel manager | Med | Med | Medium | **P2** |

### Prioritization Summary (Spoofing)

* **Most urgent (Critical, P1):** **S5** and **S6** — the agent service accepts tenant/session identity from the request body and WebSocket init frame on LAN-reachable ports with no authentication, yielding direct cross-tenant data access and session hijacking. These are the highest-leverage spoofing defects.
* **High (P1):** A coherent cluster around **shared static keys and weak defaults** (S1, S2, S3, S4, S12) and **unauthenticated high-power surfaces** (S8 Kali PTY proxy, S10 MCP tooling, S11 confused-deputy key fetch, S13 default-credentialed datastores). Each individually permits impersonation of a higher-trust principal; together they form chains (e.g., S3 → S2 → S11 harvests every tenant's LLM keys from a single compromised scanner).
* **Lower / conditional (Medium, P2):** **S7**, **S9**, and **S14** are real but conditional — S7 and S9 require a preceding foothold or amplify other threats, and S14 is bounded by what the tunnel sync exposes and by loopback reachability for the direct `:8015` path.

A recurring root cause across the P1 set is **possession-equals-identity**: RedAmon authenticates *what you hold* (a shared key) or *what you claim* (a body field / init frame), rather than *who you are* (a bound, verified session). Mitigation design (deferred to the next phase) should focus there.

---

## Notes

* All Spoofing findings above are grounded in **actual repository evidence** (cited file and line ranges); no abstract or hypothetical risks were included.
* **No mitigations or recommendations** are provided in this document — control design is deferred to the next phase, per the staged threat-model process.
* The committed `.env` supplies strong random values for `AUTH_SECRET`, `INTERNAL_API_KEY`, and `ORCHESTRATOR_API_KEY`; the associated threats (S1, S2, S12) are therefore realized primarily on deployments that fail to supply these, while `NEO4J_PASSWORD`/`POSTGRES_PASSWORD`/`GVM_PASSWORD` (S13) are **not** set in `.env` and use weak defaults unless overridden. Likelihood ratings reflect the local/LAN deployment assumption from Section 1.
* **Next session:** complete deep analysis for **T – Tampering**, **R – Repudiation**, **I – Information Disclosure**, **D – Denial of Service**, and **E – Elevation of Privilege** (≥10 threats each), then extend the Threat Prioritization table with those IDs.

---

*Generated from static analysis of the RedAmon repository (v5.1.0). This document covers STRIDE Spoofing threat enumeration only; remaining categories and all mitigations are deferred to subsequent threat-model stages.*

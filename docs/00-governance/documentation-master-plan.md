# ELYON QUANT — Plan Maestro de Documentación

> **Propósito:** definir **todos** los documentos que se crean **antes de
> escribir código de producto**, el orden exacto en que se construyen, quién los
> posee y cómo se aprueban. Este documento es el **índice raíz** y el contrato de
> trabajo de la fase de diseño.
>
> Metodología inspirada en cómo documentan y deciden Stripe (RFCs, API-first),
> Linear (specs concisas y opinadas), Notion (single source of truth), Palantir
> (rigor de dominio) y OpenAI (specs de sistema + model/spec cards).

---

## 1. Filosofía y metodología

1. **Docs-as-code.** Toda la documentación vive en el repo (`/docs`), en
   Markdown, versionada y **revisada por Pull Request** igual que el código.
2. **Single Source of Truth.** El **Product Bible** es la fuente de verdad de
   producto; el **System Architecture** lo es de lo técnico; el **Glosario /
   Lenguaje Ubicuo** lo es de la terminología. Nada se contradice entre docs.
3. **Phase-Gate (Stage-Gate).** El diseño avanza por **fases**. Una fase no
   empieza hasta que la anterior está **Aprobada**. Cada fase tiene un *gate* con
   criterios de salida explícitos.
4. **RFC para decisiones significativas.** Cambios de rumbo → **RFC** → discusión
   → decisión → se destila en un **ADR**. (Ver `rfc-process.md`.)
5. **Cada documento es un producto.** Tiene *owner*, *reviewers*, estado,
   versión y fecha. Un documento sin owner no existe.
6. **Conciso y opinado > exhaustivo y tibio.** Preferimos specs que **deciden**
   (estilo Linear) a documentos que enumeran opciones sin cerrar.
7. **Trazabilidad.** Requisito → diseño → spec de motor → test. Se mantiene una
   **matriz de trazabilidad** (último doc de la fase D7).

### Estados de un documento

| Estado | Símbolo | Significado |
|--------|---------|-------------|
| Pendiente | ⬜ | No iniciado |
| Borrador | 🟨 | Escrito, en evolución |
| En revisión | 🟦 | En PR / revisión formal |
| Aprobado | 🟩 | Firmado por owner + reviewers; base para la siguiente fase |
| Obsoleto | ⬛ | Superseded por otro doc/ADR |

### Front-matter obligatorio de cada documento

```yaml
title: <Título>
id: <p.ej. PRD-001>
owner: <rol responsable>
reviewers: [<roles>]
status: draft | in-review | approved
version: 0.1
last_updated: YYYY-MM-DD
supersedes: <id o —>
```

### Roles (owners)

`CEO/Founder` · `PM` (Product) · `Design Lead` · `Brand Lead` ·
`CTO/Principal Architect` · `Quant Lead` · `ML Lead` · `Security Lead` ·
`Platform/SRE Lead` · `QA Lead` · `Eng Lead`. En un equipo pequeño una persona
asume varios roles, pero el **rol** que aprueba queda registrado.

---

## 2. Taxonomía de la documentación (estructura destino de `/docs`)

```
docs/
├── 00-governance/     # Cómo trabajamos y decidimos (meta-docs)
├── 01-product/        # Por qué y qué (estrategia y producto)
├── 02-design/         # Cómo se ve y se siente (marca, UI/UX)
├── 03-architecture/   # Cómo se construye (arquitectura y dominio)
├── 04-engines/        # Especificaciones de los motores core
├── 05-data/           # Datos y persistencia
├── 06-api/            # Contratos: API y eventos
├── 07-security/       # Seguridad, licencias, cumplimiento
├── 08-engineering/    # Estándares de ingeniería y calidad
├── 09-operations/     # Despliegue, observabilidad, resiliencia
├── adr/               # Architecture Decision Records
└── domain/            # Lenguaje ubicuo / event storming
```

> **Nota de migración:** en la fase de arquitectura ya se crearon documentos bajo
> `docs/architecture/`, `docs/adr/` y `docs/domain/`. El **primer paso de D0** es
> reubicarlos en esta taxonomía numerada (ver mapeo en cada tabla, columna
> "Actual"). No se pierde nada; se promueve y reorganiza.

---

## 3. Fases de documentación y **orden exacto de construcción**

Ocho fases-gate (**D0 → D7**). Se construyen **en orden**. Dentro de cada fase,
los documentos siguen el orden listado (respetando dependencias).

Leyenda de columnas: **ID** · **Documento** · **Propósito** · **Owner** ·
**Depende de** · **Estado** (⬜/🟨/🟩) · **Actual** (si ya existe algo).

---

### 🟦 Fase D0 — Gobierno y Fundaciones de Documentación
*Gate de salida: reglas de juego aprobadas; cualquiera sabe cómo escribir,
revisar y aprobar un documento.*

| ID | Documento | Propósito | Owner | Depende de | Estado | Actual |
|----|-----------|-----------|-------|-----------|--------|--------|
| GOV-000 | **Documentation Master Plan** | Este índice raíz y metodología | CTO+PM | — | 🟨 | *este archivo* |
| GOV-001 | **Documentation Standards** | Cómo se escribe/versiona/aprueba un doc; plantillas | CTO | GOV-000 | ⬜ | — |
| GOV-002 | **RFC Process** | Cómo se proponen y deciden cambios significativos | CTO | GOV-001 | ⬜ | — |
| GOV-003 | **ADR Process & Log** | Registro de decisiones de arquitectura | CTO | GOV-002 | 🟨 | `adr/README.md` + ADR-0001..0005 |
| GOV-004 | **Ways of Working / Team Charter** | Roles, RACI, ceremonias, DoR/DoD, comunicación | CEO+CTO | GOV-001 | ⬜ | — |
| GOV-005 | **Glossary / Ubiquitous Language** | Terminología única del dominio | PM+Quant | — | 🟨 | `domain/ubiquitous-language.md` |
| GOV-006 | **Decision Log (no-arquitectura)** | Decisiones de producto/negocio | PM | GOV-002 | ⬜ | — |

---

### ⬜ Fase D1 — Estrategia y Producto (*Por qué* y *Qué*)
*Gate de salida: visión, público, modelo de negocio y requisitos de producto
aprobados. El Product Bible es el norte de todo lo demás.*

| ID | Documento | Propósito | Owner | Depende de | Estado |
|----|-----------|-----------|-------|-----------|--------|
| PRD-000 | **Product Vision** | Visión a 3-5 años, misión, principios, "por qué existimos" | CEO | GOV-* | ⬜ |
| PRD-001 | **Product Bible** | SSOT de producto: qué es, qué no es, pilares, alcance | PM | PRD-000 | ⬜ |
| PRD-002 | **Market & Competitive Analysis** | Mercado, competidores, posicionamiento, diferenciación | PM | PRD-000 | ⬜ |
| PRD-003 | **Personas & Jobs-to-be-Done** | Usuarios objetivo y sus trabajos por resolver | PM | PRD-002 | ⬜ |
| PRD-004 | **User Journeys & Flows** | Recorridos clave de punta a punta | PM+Design | PRD-003 | ⬜ |
| PRD-005 | **Business Model / Pricing & Packaging** | Planes, tiers, monetización, unit economics | CEO+PM | PRD-002 | ⬜ |
| PRD-006 | **Product Requirements Document (master)** | Requisitos funcionales globales, épicas | PM | PRD-001 | ⬜ |
| PRD-007 | **Feature PRDs (por módulo)** | PRD específico por bounded context (12) | PM | PRD-006 | ⬜ |
| PRD-008 | **Non-Functional Requirements** | Rendimiento, disponibilidad, seguridad, cumplimiento (medibles) | CTO+PM | PRD-006 | ⬜ |
| PRD-009 | **Success Metrics / North Star & KPIs** | Métrica norte, KPIs, criterios de éxito | PM | PRD-005 | ⬜ |
| PRD-010 | **Product Roadmap** | Roadmap de producto (no técnico) por releases | PM | PRD-006 | ⬜ |

---

### ⬜ Fase D2 — Marca y Experiencia (*Cómo se ve y se siente*)
*Gate de salida: identidad de marca y sistema de diseño aprobados; todo mockup
posterior usa estos tokens y patrones.*

| ID | Documento | Propósito | Owner | Depende de | Estado |
|----|-----------|-----------|-------|-----------|--------|
| DES-000 | **Brand Guidelines** | Logo, paleta, tipografía, iconografía, uso | Brand | PRD-001 | ⬜ |
| DES-001 | **Voice & Tone / Content Guidelines** | Cómo comunica el producto (copy, microcopy) | Brand+PM | DES-000 | ⬜ |
| DES-002 | **UX Principles & Interaction Patterns** | Principios de UX, patrones, estados, errores | Design | PRD-004 | ⬜ |
| DES-003 | **UI Design System** | Tokens, componentes, layout, temas (light/dark) | Design | DES-000 | ⬜ |
| DES-004 | **Accessibility Standards (a11y)** | WCAG objetivo, contraste, teclado, ARIA | Design | DES-003 | ⬜ |
| DES-005 | **Information Architecture & Navigation** | Estructura, navegación, jerarquía de la app | Design+PM | PRD-004 | ⬜ |
| DES-006 | **Dashboard Specification** | Especificación del dashboard: paneles, datos, tiempo real | Design+PM | DES-003, PRD-007 | ⬜ |
| DES-007 | **Charting & Data-Viz Spec** | Gráficos financieros, indicadores, overlays SMC | Design+Quant | DES-006 | ⬜ |
| DES-008 | **Prototypes / Wireflows** | Prototipos navegables de los flujos clave | Design | DES-002 | ⬜ |

---

### 🟦 Fase D3 — Arquitectura y Diseño Técnico (*Cómo se construye*)
*Gate de salida: arquitectura, requisitos técnicos y modelo de dominio
aprobados. Base para las specs de motores.*

| ID | Documento | Propósito | Owner | Depende de | Estado | Actual |
|----|-----------|-----------|-------|-----------|--------|--------|
| ARC-000 | **System Architecture Overview** | Visión, C4, estilo, capas, comunicación | CTO | PRD-008 | 🟨 | `architecture/00` |
| ARC-001 | **Repository & Module Structure** | Monorepo, carpetas, estructura por capas | CTO | ARC-000 | 🟨 | `architecture/01` |
| ARC-002 | **Bounded Contexts & Context Map** | DDD estratégico, subdominios, relaciones | CTO+PM | ARC-000, GOV-005 | 🟨 | `architecture/02` |
| ARC-003 | **Inter-Module Dependencies** | Reglas, DAG, contratos, consistencia | CTO | ARC-002 | 🟨 | `architecture/03` |
| ARC-004 | **Technical Requirements** | NFRs formalizados y verificables (SLOs, latencia) | CTO | PRD-008 | ⬜ | — |
| ARC-005 | **Technology Stack + ADRs** | Stack y decisiones estructurales justificadas | CTO | ARC-000 | 🟨 | `architecture/04` + `adr/` |
| ARC-006 | **Domain Model (DDD táctico)** | Agregados, entidades, VOs, invariantes por contexto | CTO+Quant | ARC-002 | ⬜ | — |
| ARC-007 | **Event Catalog / Event Storming** | Eventos de dominio, comandos, políticas | CTO+Quant | ARC-006 | ⬜ | — |
| ARC-008 | **Integration & Contract Design** | Estilos de comunicación, sagas, outbox/inbox | CTO | ARC-003, ARC-007 | ⬜ | (parcial en `03`) |

---

### ⬜ Fase D4 — Especificaciones de Motores Core (*El corazón del producto*)
*Gate de salida: cada motor especificado (entradas/salidas, algoritmos,
invariantes, límites, métricas) y revisado por Quant/ML. Orden por dependencia:
datos → estrategia/SMC/IA → backtest → riesgo → ejecución → portfolio.*

| ID | Documento | Propósito | Owner | Depende de | Estado |
|----|-----------|-----------|-------|-----------|--------|
| ENG-000 | **Market Data Engine Bible** | **SSOT de datos**: ingesta/ACL, tick/candle/MTF builder, sesión/spread/volumen/ATR, snapshots, histórico versionado, calidad, dedup/orden/gaps, no-repaint, reproducibilidad | Platform/Data | ARC-006 | 🟨 → `04-engines/market-data-engine-bible.md` |
| ENG-001 | **Trading / Strategy Engine Spec** | Modelo de estrategia, señales, indicadores, ciclo de vida | Quant | ENG-000 | 🟨 → `04-engines/trading-engine-bible.md` |
| ENG-002 | **Smart Money Engine Spec** | Conceptos SMC/ICT: order blocks, liquidez, FVG, BOS/CHoCH, estructura + **Fibonacci Institucional (D32)** | Quant | ENG-000, ENG-001 | 🟨 v0.2 → `04-engines/smart-money-engine-bible.md` (31 detectores, contrato de 18 campos + integración) |
| ENG-003 | **AI Engine Spec** | Modelos, features, entrenamiento, inferencia, MLOps, guardrails | ML | ENG-000, ENG-001 | ⬜ |
| ENG-004 | **Backtesting Engine Spec** | Simulación determinista, reproducibilidad, walk-forward, métricas | Quant | ENG-001, ENG-002 | ⬜ |
| ENG-005 | **Risk Engine Spec** | Pre/in/post-trade, límites (op/día/sem/mes/símbolo/sesión/estrategia/correlación), exposición, drawdown, kill-switch, cooldown, funded accounts, sizing, dinámico | Risk | ENG-001, ENG-011 | 🟨 → `04-engines/risk-engine-bible.md` |
| ENG-006 | **Execution Engine Bible (OMS)** | Ciclo de vida completo de la orden, máquina de estados, routing, fills, position mgmt, CQRS+ES, outbox, sagas, exactly-once, circuit breakers, health, DLQ, observabilidad, recovery; +ADR | Execution | ENG-005, ENG-000 | 🟨 → `04-engines/execution-engine-bible.md` |
| ENG-007 | **Portfolio & Analytics Engine Spec** | Position keeping, PnL, métricas, tearsheets, atribución | Quant | ENG-006 | ⬜ |
| ENG-008 | **Broker/Exchange Connectivity Spec** | ACLs: MT5, IB, Binance…; mapeo de contratos | CTO | ENG-006 | ⬜ |
| ENG-009 | **Decision Replay Engine Spec** | Registro de **todas** las decisiones (ejecutadas + descartadas) y reproducción paso a paso | CTO | ENG-001, ENG-002 | 🟨 → `04-engines/decision-replay-engine-spec.md` |
| ENG-010 | **Explainable AI (XAI) Standard** | Invariante de núcleo: toda decisión explicable (qué detectó/confirmó/descartó, pesos, score, reglas, vetos) | ML | ENG-001, ENG-009 | 🟨 → `04-engines/explainable-ai-spec.md` |
| ENG-011 | **Market Context Engine (+ Market DNA)** | **Primer gate**: régimen/contexto/calidad → Context Score (0–100); Market DNA por activo (adapta filtros, no reglas) | Quant | ENG-000 | 🟨 → `04-engines/market-context-engine-spec.md` |

---

### ⬜ Fase D5 — Datos, API, Seguridad y Licencias (*Contratos y protección*)
*Gate de salida: diseño de datos, contratos de API/eventos, seguridad,
licenciamiento y cumplimiento aprobados.*

| ID | Documento | Propósito | Owner | Depende de | Estado | Actual |
|----|-----------|-----------|-------|-----------|--------|--------|
| DAT-000 | **Database Design** | Esquemas por módulo, modelo físico, índices, particionado, series temporales | CTO | ARC-006, ENG-* | ⬜ | — |
| DAT-001 | **Data Governance & Retention** | Clasificación, retención, PII, calidad, linaje | Security+CTO | DAT-000 | ⬜ | — |
| API-000 | **API Design (REST/WS)** | Recursos, versionado, paginación, errores (RFC7807), WS | CTO | ARC-008 | ⬜ | — |
| API-001 | **Async / Event API Design** | Topics, envelopes, esquemas Avro, compatibilidad | CTO | ARC-007 | 🟡 → `06-api/core-contracts-v1.0.md` (Core Contracts v1.0: C1–C9 congela interfaces inter-motor + versionado/compat/deprecación/testing/breaking) | — |
| API-002 | **Public API & SDK Design** | API pública, rate-limits por plan, SDKs Python/TS | CTO+PM | API-000 | ⬜ | — |
| SEC-000 | **Security Design & Threat Model** | AuthN/Z, multi-tenant, secretos, STRIDE, defensa en profundidad | Security | ARC-000, DAT-001 | 🟨 | `architecture/11` |
| SEC-001 | **Licensing System Design** | Modelo de licencias, activación, verificación, anti-abuso, entitlements | CTO+PM | SEC-000, PRD-005 | ⬜ | — |
| SEC-002 | **Compliance & Regulatory** | GDPR/CCPA, financiero (MiFID/otros), auditoría | Security+CEO | SEC-000 | ⬜ | — |
| SEC-003 | **Privacy Design** | Privacidad por diseño, consentimiento, derechos del usuario | Security | SEC-002 | ⬜ | — |

---

### 🟦 Fase D6 — Ingeniería, Calidad y Operaciones (*Cómo trabajamos y operamos*)
*Gate de salida: estándares de ingeniería y plan operativo aprobados. Todo listo
para producir código con calidad y operarlo.*

**Ingeniería y calidad**

| ID | Documento | Propósito | Owner | Depende de | Estado | Actual |
|----|-----------|-----------|-------|-----------|--------|--------|
| ENGX-000 | **Coding Standards** | Clean Code, SOLID, patrones, manejo de errores | Eng Lead | ARC-001 | 🟨 | `architecture/06` |
| ENGX-001 | **Naming Convention** | Nomenclatura por lenguaje y capa | Eng Lead | ENGX-000 | 🟩 | `architecture/07` |
| ENGX-002 | **Git Workflow** | Trunk-based, commits, PRs, protección de ramas | Eng Lead | GOV-004 | 🟨 | `architecture/08` |
| ENGX-003 | **Release Workflow & Versioning** | SemVer, changelog, releases por servicio | Eng Lead | ENGX-002 | ⬜ | — |
| ENGX-004 | **Code Review Guidelines** | Qué se revisa, CODEOWNERS, estándar de aprobación | Eng Lead | ENGX-000 | ⬜ | — |
| ENGX-005 | **Testing Strategy** | Pirámide, contract testing, cobertura, mutation | QA | ARC-008 | 🟨 | `architecture/09` |
| ENGX-006 | **Quality Assurance Plan** | Proceso QA, criterios de calidad, gates de release | QA | ENGX-005 | ⬜ | — |
| ENGX-007 | **Definition of Ready / Done** | Criterios de entrada/salida de una tarea | PM+Eng | GOV-004 | ⬜ | (parcial en `06`) |

**Operaciones (SRE / Platform)**

| ID | Documento | Propósito | Owner | Depende de | Estado | Actual |
|----|-----------|-----------|-------|-----------|--------|--------|
| OPS-000 | **Infrastructure Design** | Cloud, red, K8s, IaC, entornos | Platform | ARC-000 | ⬜ | — |
| OPS-001 | **Deployment Strategy** | CI/CD, GitOps, progresivo, zero-downtime | Platform | OPS-000, ENGX-003 | 🟨 | `architecture/10` |
| OPS-002 | **Environments & Configuration** | Entornos, config 12-factor, feature flags | Platform | OPS-000 | ⬜ | — |
| OPS-003 | **Monitoring & Observability** | Métricas, tracing, dashboards, SLIs | Platform | OPS-001 | ⬜ | — |
| OPS-004 | **Logging Standards** | Logs estructurados, correlación, PII, retención | Platform | OPS-003 | ⬜ | — |
| OPS-005 | **Alerting & On-Call / Incident Mgmt** | Alertas, on-call, severidades, post-mortems | Platform | OPS-003 | ⬜ | — |
| OPS-006 | **Performance Targets (SLIs/SLOs)** | Objetivos de latencia/throughput, error budgets | Platform+CTO | ARC-004 | ⬜ | (parcial en `12`) |
| OPS-007 | **Scalability Plan** | Escalado por ejes, datos, resiliencia, cells | CTO | OPS-006 | 🟨 | `architecture/12` |
| OPS-008 | **Disaster Recovery & BCP** | RPO/RTO, backups, restore drills, multi-región | Platform | OPS-001 | ⬜ | — |
| OPS-009 | **Cost Management / FinOps** | Presupuesto, autoscaling, tiering, unit cost | Platform+CEO | OPS-007 | ⬜ | — |
| OPS-010 | **Runbooks** | Procedimientos de incidente (broker caído, kill-switch…) | Platform | OPS-005 | ⬜ | — |

---

### ⬜ Fase D7 — Gate de Construcción (*Go-to-Build*)
*Gate de salida: plan de ejecución, riesgos y trazabilidad aprobados. Cuando
esta fase cierra, **empieza el código**.*

| ID | Documento | Propósito | Owner | Depende de | Estado | Actual |
|----|-----------|-----------|-------|-----------|--------|--------|
| BLD-000 | **Delivery / Execution Plan** | Cómo construir el producto paso a paso | CTO+PM | Todas | 🟨 | `architecture/13` |
| BLD-001 | **Per-Phase Build Plan** | Plan detallado de cada fase de construcción | Eng Lead | BLD-000 | ⬜ | — |
| BLD-002 | **Risk Register** | Riesgos técnicos/producto/negocio y mitigaciones | PM+CTO | Todas | ⬜ | — |
| BLD-003 | **Traceability Matrix** | Requisito → diseño → spec → test | QA+PM | Todas | ⬜ | — |
| BLD-004 | **Go-to-Build Checklist** | Criterios finales para aprobar el inicio del código | CEO+CTO | Todas | ⬜ | — |

---

## 4. Resumen del orden exacto (vista ejecutiva)

```
D0  Gobierno de docs        →  reglas del juego
D1  Estrategia y Producto   →  por qué / qué / para quién
D2  Marca y Experiencia     →  cómo se ve y se siente
D3  Arquitectura y Dominio  →  cómo se construye (base técnica)
D4  Motores Core            →  el corazón: datos→trading→SMC→IA→backtest→riesgo→ejecución
D5  Datos · API · Seguridad →  contratos y protección
D6  Ingeniería y Operaciones→  cómo trabajamos y operamos
D7  Go-to-Build             →  plan, riesgos, trazabilidad  ⟶  EMPIEZA EL CÓDIGO
```

**Regla de oro:** ninguna fase empieza sin la anterior en 🟩 **Aprobado**. Dentro
de una fase, los documentos pueden avanzar en paralelo salvo las dependencias
marcadas en la columna "Depende de".

### Camino crítico (si hay que priorizar)

`GOV-000/005 → PRD-000/001 → ARC-000/002/006 → ENG-000/001/005/006 →
DAT-000 → API-000 → SEC-000/001 → OPS-001/006 → BLD-000/004`.
Todo lo demás enriquece, pero esta espina dorsal es la mínima para poder
construir con seguridad.

---

## 5. Proceso de revisión y aprobación (gate)

1. **Draft (🟨):** el *owner* redacta siguiendo la plantilla.
2. **Review (🟦):** PR con reviewers asignados (CODEOWNERS por carpeta de docs).
   Comentarios resueltos; conflictos → **RFC**.
3. **Approved (🟩):** owner + reviewers aprueban; se actualiza `status` y versión.
4. **Gate de fase:** cuando **todos** los documentos de la fase están 🟩, el CTO
   y el PM firman el *gate* y se abre la siguiente fase.
5. **Cambios posteriores:** un doc aprobado solo cambia por nueva versión con su
   PR; decisiones que lo alteren estructuralmente generan un **ADR/RFC**.

---

## 6. Métrica de progreso

Progreso de la fase de diseño = **% de documentos en 🟩** ponderado por fase.
Se reporta en cada revisión. **No se escribe código de producto hasta cerrar
D7.** (El único "código" permitido antes es andamiaje de tooling/CI si un doc de
D6 lo aprueba explícitamente.)

---

## 7. Documentos ya existentes (de la fase de arquitectura)

Estos documentos ya están redactados (🟨 Borrador) y se **promueven** dentro de
este plan; el primer paso de D0 es reubicarlos en la taxonomía numerada:

- `docs/architecture/00..13` → ARC-000..003, ARC-005, ENGX-000/001/002/005,
  OPS-001/007, SEC-000, BLD-000.
- `docs/adr/*` → GOV-003 + ADR-0001..0005.
- `docs/domain/ubiquitous-language.md` → GOV-005.

> Reubicarlos y completar los ~50 documentos pendientes es el trabajo de las
> fases D0–D7. Este Plan Maestro (GOV-000) es el primero y queda en 🟨 hasta que
> se apruebe formalmente el gate D0.

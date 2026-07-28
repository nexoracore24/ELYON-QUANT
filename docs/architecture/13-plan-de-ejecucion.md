# 13 — Plan de Ejecución: cómo construirlo paso a paso

> Cómo pasar de "cero código" a una plataforma sólida **sin ahogarse** y sin
> comprometer la arquitectura. La regla: **vertical slices que atraviesan todas
> las capas**, no construir "toda la capa de datos" antes de ver valor.

## 0. Cómo leer este plan

- Cada **paso** deja algo **desplegable y demostrable**.
- El orden respeta las **dependencias del [context map](02-modulos-y-bounded-contexts.md)**:
  primero lo que otros necesitan (`iam`, `market_data`), luego lo que consume.
- No se pasa de paso sin **tests + observabilidad + CI verde** (ver [DoD](06-convenciones-desarrollo.md#11-definition-of-done-dod)).

---

## Bloque A — Fundaciones (Fase 0)

**Paso 1 · Andamiaje del repo**
- Estructura de carpetas de [01](01-organizacion-repositorio.md), `Makefile`,
  `docker-compose` (Postgres, Redis, Redpanda, Temporal), pre-commit.
- CI base: lint, type-check, tests, build. Ramas protegidas.
- *Salida:* `make up` + `make test` funcionan; pipeline verde.

**Paso 2 · Plataforma transversal (`platform/`) + `shared_kernel`**
- Config tipada, contenedor DI, buses (command/query/event), Outbox base,
  telemetría (OTel), middleware de seguridad/tenant, error handler central.
- `shared_kernel`: `AggregateRoot`, `Entity`, `DomainEvent`, VOs (`Money`,
  `Symbol`, `Price`, `Quantity`), jerarquía de errores.
- *Salida:* base sobre la que todo módulo se enchufa igual.

**Paso 3 · Walking skeleton con `iam`**
- Un módulo completo end-to-end (las 4 capas): registro/login, tenant, RBAC
  mínimo, un evento (`UserRegistered`) publicado vía Outbox y consumido.
- Contrato en `/contracts` + tipos generados. Tests en cada capa.
- *Salida:* prueba viva de que la arquitectura funciona de punta a punta.
  **Este es el patrón que se replica en todos los módulos.**

---

## Bloque B — MVP vertical de trading (Fase 1)

> Meta del bloque: **estrategia → backtest → paper → live (MT5)** con riesgo.

**Paso 4 · `market_data` (base)**
- Catálogo de instrumentos, ingesta OHLCV histórica (1 proveedor), acceso a
  series; `market-data-ingestor` para tiempo real de 1 feed.
- *Salida:* datos disponibles para backtest y ejecución.

**Paso 5 · `strategy_lab`**
- Definir/versionar estrategias (parámetros, indicadores), ciclo de vida.
- Eventos `StrategyVersionCreated/Validated`.
- *Salida:* un usuario define una estrategia versionada.

**Paso 6 · `backtesting` + `quant-workers`**
- Motor determinista (reloj/semilla inyectables), jobs vía Temporal, resultados
  y *tearsheet* a S3. Test de **reproducibilidad al bit**.
- *Salida:* backtest reproducible con métricas y tearsheet.

**Paso 7 · `portfolio` + `risk` (mínimos)**
- `portfolio`: posiciones y PnL a partir de fills.
- `risk`: límites pre-trade esenciales + kill-switch; contrato `RiskApproved/
  RiskRejected`.
- *Salida:* control de riesgo listo para conectar a ejecución.

**Paso 8 · `execution` + `execution-gateway` (paper)**
- OMS: ciclo de vida de orden, idempotencia. Modo **paper** primero (simulación
  de fills). `risk` bloqueante integrado.
- *Salida:* operar en **paper** de punta a punta.

**Paso 9 · `execution` live sobre MT5 (ACL)**
- Adaptador MT5 en `execution-gateway` (ACL), reconciliación con broker.
- *Salida:* operar en **real** sobre MT5 con riesgo y auditoría.

**Paso 10 · SaaS mínimo viable**
- `iam` (ya) + `billing` (Stripe) + `notifications` (email) + `audit` ledger.
- Frontend `apps/web`: estrategias, backtests, operativa en vivo, PnL.
- *Salida:* **producto SaaS end-to-end**: alta, pago, crear estrategia,
  backtest, paper, live. **Fin de Fase 1.**

---

## Bloque C — Robustez y multi-broker (Fase 2)

**Paso 11 · Conectividad adicional** — IB y Binance como **nuevos ACLs** (sin
tocar el dominio de `execution`). Valida que la abstracción de broker es correcta.

**Paso 12 · `analytics`** — read models/CQRS: métricas avanzadas, atribución,
comparativas, reporting; consume eventos.

**Paso 13 · Resiliencia y datos a escala** — outbox/inbox en todos los módulos,
DLQs, idempotencia end-to-end; Timescale/ClickHouse a escala; calidad de datos.

**Paso 14 · Endurecimiento multi-tenant** — RLS, cuotas por plan, SLOs formales,
runbooks, on-call, chaos testing. **Fin de Fase 2.**

---

## Bloque D — Ecosistema (Fase 3)

**Paso 15 · `marketplace`** — publicar/descubrir/suscribir/monetizar,
*revenue share*, copy-trading.

**Paso 16 · API pública + SDKs** — contratos versionados, rate-limits por plan,
SDKs Python/TS para autoría externa.

**Paso 17 · Backtesting a escala** — walk-forward, optimización, paralelismo
masivo. **Fin de Fase 3.**

---

## Bloque E — Escala internacional (Fase 4)

**Paso 18 · Extracción de servicios core** — `execution`, `market_data`,
`backtesting` a servicios propios (criterio de extracción de [05](05-roadmap-tecnico.md)).

**Paso 19 · Multi-región + cumplimiento** — data locality, DR, SOC 2/ISO 27001,
service mesh (mTLS), cell-based architecture.

---

## Cómo dividir el trabajo entre personas/equipos

- **Por bounded context**: cada módulo puede ser propiedad de una persona/equipo
  (CODEOWNERS). Las fronteras del context map = límites de responsabilidad.
- **Contract-first**: se acuerda el contrato (`/contracts`) antes de implementar;
  productores y consumidores avanzan en paralelo contra el contrato + *fakes*.
- **Vertical antes que horizontal**: cada quien entrega su *slice* completo (4
  capas) en lugar de repartirse por capas técnicas.
- **Core con doble revisión**: `execution`, `risk`, `execution-gateway` exigen 2
  revisores y cobertura/mutation testing más altos.

## Orden de prioridad si hay que recortar

Si el tiempo aprieta, este es el **camino crítico mínimo** para tener algo real:
`iam` → `market_data` → `strategy_lab` → `backtesting` → `risk` → `execution`
(paper→MT5) → `portfolio` → `billing` → frontend.
Todo lo demás (`marketplace`, `analytics` avanzada, multi-broker, multi-región)
es incremental sobre esa columna vertebral.

---

## Checklist de arranque (primer día de código)

1. Crear andamiaje (Paso 1) y CI.
2. Levantar `platform/` + `shared_kernel` (Paso 2).
3. Implementar `iam` como *walking skeleton* (Paso 3).
4. A partir de ahí, **replicar el patrón** módulo a módulo siguiendo este plan.

> Recordatorio final: la arquitectura de estos 14 documentos existe para que
> este plan se pueda ejecutar **incrementalmente y sin reescrituras**. Cada paso
> se apoya en las fronteras y contratos definidos, no en atajos que haya que
> pagar después.

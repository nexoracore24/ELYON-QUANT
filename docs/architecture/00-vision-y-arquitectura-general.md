# 00 — Visión y Arquitectura General

## 1. Visión de producto

ELYON QUANT es una **plataforma de trading algorítmico como servicio (SaaS)**,
multi-tenant, diseñada para tres perfiles de usuario:

- **Quant / Trader retail avanzado** — diseña, backtestea y opera estrategias.
- **Trading firm / fondo pequeño** — equipos con control de riesgo y auditoría.
- **Creador de estrategias** — publica y monetiza estrategias en el Marketplace.

El objetivo no es un bot, sino un **ecosistema**: datos → estrategia →
validación → ejecución → riesgo → analítica → monetización. La ventaja
competitiva vive en tres capacidades *core*: el **motor de backtesting de alta
fidelidad**, el **motor de ejecución de baja latencia** y el **sistema de
gestión de riesgo** en tiempo real.

### Atributos de calidad prioritarios (en orden)

1. **Correctitud y determinismo** — un backtest debe ser reproducible al bit;
   una orden nunca se ejecuta dos veces.
2. **Seguridad** — manejamos credenciales de broker y capital real.
3. **Disponibilidad** — la ejecución en vivo es *mission-critical* (SLO 99.95 %).
4. **Baja latencia** en el *hot path* de ejecución (p99 < 50 ms plataforma;
   sub-ms en el gateway).
5. **Escalabilidad** horizontal por tenant y por símbolo.
6. **Mantenibilidad y velocidad de entrega**.

---

## 2. Estilo arquitectónico

**Modular Monolith event-driven, con dominios aislados listos para extraerse
a microservicios.**

Por qué esta elección (decisión de CTO, no de moda):

- Un microservicio por *feature* desde el día 1 mata la velocidad de un equipo
  pequeño y multiplica el coste operativo. Empezamos como **monolito modular**:
  un solo *deployable* para la plataforma transaccional, pero con **fronteras
  de módulo estrictas** (cada módulo = un *bounded context* con sus propias
  capas y su propio esquema de datos).
- Los módulos se comunican **solo** por contratos explícitos: llamadas a la
  capa de aplicación vía interfaces (in-process) o **eventos de dominio**
  a través de un bus. Nunca por acceso directo a tablas de otro módulo.
- Cuando un módulo necesita escalar o desplegarse por separado (p.ej.
  `execution`, `market-data`, `backtesting`), se **extrae** a servicio propio
  sin reescribir el dominio: solo cambia el *transport* del contrato
  (in-process → red). Esto es posible porque las fronteras ya existen.

Además, tres componentes nacen ya como **servicios independientes** porque sus
requisitos no-funcionales son radicalmente distintos al del monolito:

| Servicio | Por qué separado desde el día 1 |
|----------|---------------------------------|
| `execution-gateway` | Latencia sub-ms, escrito en Rust, perfil de despliegue crítico. |
| `market-data-ingestor` | Alto *throughput* de streaming, escala por feed/símbolo. |
| `quant-workers` | Cómputo intensivo (backtests/ML), escala elástica y aislada. |

---

## 3. Diagrama de contexto (C4 — Nivel 1)

```
                         ┌───────────────────────────────┐
      Traders / Quants ──► ELYON QUANT (SaaS multi-tenant)│
      Creadores        ──►                                │
      Back-office      ──►  Datos→Estrategia→Backtest→    │
                         │  Paper→Ejecución→Riesgo→        │
                         │  Portfolio→Analytics→Market     │
                         └───┬───────────┬────────────┬───┘
                             │           │            │
                   ┌─────────▼──┐  ┌─────▼─────┐  ┌───▼────────┐
                   │  Brokers/  │  │  Market   │  │  Payment/  │
                   │  Exchanges │  │  Data      │  │  Billing   │
                   │ MT5/IB/Bin │  │  Providers │  │ (Stripe)   │
                   └────────────┘  └───────────┘  └────────────┘
```

## 4. Diagrama de contenedores (C4 — Nivel 2)

```
┌──────────────────────────────── Edge ─────────────────────────────────┐
│  apps/web (Next.js)   apps/admin (back-office)   Public API (REST/WS)  │
└───────────────┬───────────────────────────────────────────────────────┘
                │  API Gateway (authN, rate-limit, WAF)
     ┌──────────▼───────────────────────────────────────────────────┐
     │                services/platform-api  (MODULAR MONOLITH)      │
     │                                                               │
     │  IAM · Billing · Strategy Lab · Backtesting(coord) · Risk ·   │
     │  Portfolio · Execution(coord) · Marketplace · Analytics ·     │
     │  Notifications                                                │
     │  ── cada módulo: domain / application / infra / interfaces ── │
     └───┬───────────────┬─────────────────┬────────────────┬───────┘
         │ eventos        │ comandos        │ eventos         │
   ┌─────▼─────┐   ┌──────▼───────┐  ┌──────▼────────┐  ┌─────▼──────┐
   │  Event Bus │   │ execution-   │  │ market-data-  │  │ quant-     │
   │ (Kafka /   │◄──┤ gateway      │  │ ingestor      │  │ workers    │
   │ Redpanda)  │   │ (Rust,sub-ms)│  │ (streaming)   │  │ (backtest, │
   └─────┬──────┘   └──────┬───────┘  └──────┬────────┘  │  ML)       │
         │                 │ FIX/API         │           └─────┬──────┘
   ┌─────▼──────┐   ┌───────▼──────┐   ┌──────▼──────┐    ┌─────▼──────┐
   │ PostgreSQL │   │ Brokers/     │   │ TimescaleDB/│    │ Object     │
   │ (por módulo│   │ Exchanges    │   │ ClickHouse  │    │ Storage S3 │
   │  schema)   │   │              │   │ (market ts) │    │ (artifacts)│
   └────────────┘   └──────────────┘   └─────────────┘    └────────────┘
         Redis (cache, locks, rate-limit)   ·   Temporal (workflows/sagas)
```

---

## 5. Capas (Clean Architecture) — aplican **dentro de cada módulo**

```
        interfaces  →  application  →  domain  ←  infrastructure
        (drivers)      (use cases)     (core)      (adapters)
```

Regla de dependencia (inviolable): **todo apunta hacia `domain`**. El dominio
no conoce frameworks, ni la base de datos, ni HTTP.

| Capa | Responsabilidad | Contiene | Depende de |
|------|-----------------|----------|-----------|
| **domain** | Reglas de negocio puras | Entities, Value Objects, Aggregates, Domain Events, Domain Services, **Ports** (interfaces) | *nada externo* |
| **application** | Orquestación de casos de uso | Command/Query Handlers (CQRS), Application Services, DTOs, Ports de infraestructura | domain |
| **infrastructure** | Detalles técnicos | Repositorios (SQL), *message bus*, clientes de broker, cache | application, domain (implementa ports) |
| **interfaces** | Puntos de entrada | Controllers REST/GraphQL/gRPC/WS, consumidores de eventos, CLI, *schedulers* | application |

> `domain` + `application` = el **núcleo testeable sin infraestructura**.
> `infrastructure` + `interfaces` = *plugins* intercambiables.

---

## 6. Estilos de comunicación

| Escenario | Mecanismo | Justificación |
|-----------|-----------|---------------|
| Consulta/comando dentro del monolito | Llamada in-process a la interfaz de `application` de otro módulo | Simplicidad, transaccionalidad local |
| Integración entre bounded contexts | **Eventos de dominio** (async, Kafka) | Bajo acoplamiento, escalable |
| Plataforma ↔ execution-gateway | Comandos vía bus + confirmaciones | Desacoplar latencia crítica |
| Cliente ↔ plataforma | REST (sync) + WebSocket (streaming: precios, fills, PnL) | UX en tiempo real |
| Interno de alto rendimiento (futuro) | gRPC | Contratos tipados, streaming eficiente |
| Flujos de larga duración (backtests, sagas de órdenes) | **Temporal** (workflows durables) | Reintentos, compensación, estado durable |

**Patrones clave:** Outbox/Inbox para consistencia entre BD y bus,
Saga para procesos multi-módulo, CQRS donde lectura y escritura divergen,
Idempotencia obligatoria en todo consumidor de eventos.

---

## 7. Vistas relacionadas

- Módulos y fronteras → [02](02-modulos-y-bounded-contexts.md)
- Reglas de dependencia → [03](03-dependencias-entre-modulos.md)
- Tecnologías → [04](04-stack-tecnologico.md)
- Cómo empezar a construir → [13](13-plan-de-ejecucion.md)

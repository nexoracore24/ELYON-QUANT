# 01 — Organización del Repositorio

## 1. Estrategia: Monorepo polyglot

Usamos **un monorepo** gestionado con herramientas de build incremental
(Turborepo/Nx para TypeScript, Poetry/uv para Python, Cargo para Rust). Un
monorepo nos da: contratos compartidos como fuente única de verdad, refactors
atómicos entre servicios, tooling y CI homogéneos, y visibilidad total.

> Regla de oro: **la estructura de carpetas refleja la arquitectura, no el
> framework**. Un recién llegado debe entender el sistema leyendo el árbol.

---

## 2. Árbol de primer nivel

```
elyon-quant/
├── apps/                 # Aplicaciones desplegables de front / edge
│   ├── web/              # Dashboard de usuario (Next.js + TS)
│   ├── admin/            # Consola de back-office / operaciones internas
│   └── landing/          # Sitio de marketing
│
├── services/            # Runtimes backend desplegables
│   ├── platform-api/    # ⭐ MONOLITO MODULAR (Python + FastAPI)
│   ├── execution-gateway/   # Gateway de ejecución baja latencia (Rust)
│   ├── market-data-ingestor/# Handlers de feeds de mercado (Python/Rust)
│   └── quant-workers/       # Workers de backtest / ML (Python)
│
├── packages/            # Librerías compartidas versionadas internamente
│   ├── ts/              # Compartido TypeScript
│   │   ├── ui/          # Design system / componentes
│   │   ├── api-client/  # Cliente generado desde OpenAPI
│   │   ├── types/       # Tipos compartidos
│   │   └── config/      # ESLint, tsconfig, tailwind base
│   └── py/              # Compartido Python
│       ├── elyon-contracts/   # DTOs/eventos generados desde /contracts
│       ├── elyon-telemetry/   # Logging, tracing, métricas
│       ├── elyon-kernel/      # Shared Kernel (VOs comunes: Money, Symbol…)
│       └── elyon-testing/     # Fixtures, builders, utilidades de test
│
├── contracts/           # ⭐ FUENTE DE VERDAD de las interfaces
│   ├── openapi/         # API pública REST (OpenAPI 3.1)
│   ├── asyncapi/        # Eventos de dominio (AsyncAPI 2.6)
│   ├── proto/           # gRPC (comunicación interna futura)
│   └── avro/            # Esquemas de eventos (Schema Registry)
│
├── infra/               # Infraestructura como código
│   ├── terraform/       # Cloud (VPC, EKS, RDS, MSK, S3, IAM…)
│   ├── helm/            # Charts por servicio
│   ├── k8s/             # Manifiestos base / kustomize overlays
│   └── argocd/          # Definiciones GitOps
│
├── ops/                 # Operación
│   ├── runbooks/        # Procedimientos de incidencia
│   ├── dashboards/      # Grafana as code
│   ├── slo/             # Definición de SLIs/SLOs
│   └── alerts/          # Reglas de alerta
│
├── tests/               # Pruebas transversales entre servicios
│   ├── e2e/             # End-to-end de flujos de negocio
│   ├── load/            # k6 / Locust
│   └── chaos/           # Experimentos de resiliencia
│
├── tools/               # Tooling de desarrollo
│   ├── generators/      # Scaffolding de módulos, generación de código
│   ├── scripts/         # Automatizaciones
│   └── ci/              # Utilidades de pipeline
│
├── docs/                # Documentación
│   ├── architecture/    # (este directorio)
│   ├── adr/             # Architecture Decision Records
│   ├── guides/          # Guías de desarrollo / onboarding
│   └── domain/          # Ubiquitous language, event storming
│
├── .github/             # Workflows de CI/CD, templates de PR/issue
├── docker-compose.yml   # Entorno local completo
├── Makefile / Taskfile.yml   # Comandos estándar (build, test, lint, up)
└── README.md
```

---

## 3. Estructura interna del monolito modular (`services/platform-api`)

Cada **módulo = bounded context** y aplica Clean Architecture internamente.

```
services/platform-api/
└── src/elyon/
    ├── main.py                 # Composition root (ensambla la app)
    │
    ├── platform/               # Infraestructura transversal (no es dominio)
    │   ├── config/             # Settings tipados por entorno
    │   ├── di/                 # Contenedor de inyección de dependencias
    │   ├── bus/                # Command/Query/Event bus + Outbox
    │   ├── persistence/        # Base de UoW, sesión, migraciones base
    │   ├── security/           # Middleware authN/Z, contexto de tenant
    │   ├── telemetry/          # Tracing/metrics/logging wiring
    │   └── http/               # App factory, middlewares, error handlers
    │
    ├── shared_kernel/          # Conceptos compartidos por módulos (mínimo)
    │   ├── value_objects/      # Money, Currency, Symbol, Quantity, Price…
    │   ├── domain/             # AggregateRoot, Entity, DomainEvent base
    │   └── errors/             # Jerarquía de errores de dominio
    │
    └── modules/
        ├── iam/
        │   ├── domain/
        │   │   ├── model/          # Aggregates, entities, value objects
        │   │   ├── events/         # Domain events
        │   │   ├── services/       # Domain services
        │   │   └── ports/          # Repository & gateway interfaces
        │   ├── application/
        │   │   ├── commands/       # Command + handler (write)
        │   │   ├── queries/        # Query + handler (read)
        │   │   ├── dto/
        │   │   ├── ports/          # Ports hacia infraestructura
        │   │   └── services/       # Application services / policies
        │   ├── infrastructure/
        │   │   ├── persistence/    # ORM models, repos, mappers, migrations
        │   │   ├── messaging/      # Publishers / event consumers
        │   │   ├── external/       # Clientes de terceros (ACL)
        │   │   └── cache/
        │   └── interfaces/
        │       ├── rest/           # Routers/controllers FastAPI
        │       ├── events/         # Suscriptores de eventos entrantes
        │       └── cli/            # Comandos de administración
        │
        ├── billing/            # (misma estructura de 4 capas)
        ├── market_data/
        ├── strategy_lab/
        ├── backtesting/        # coordina; el cómputo vive en quant-workers
        ├── execution/          # coordina; el hot-path vive en execution-gateway
        ├── risk/
        ├── portfolio/
        ├── marketplace/
        ├── analytics/
        └── notifications/
```

### Reglas estructurales

1. **Simetría**: los 12 módulos tienen exactamente la misma forma
   (`domain / application / infrastructure / interfaces`). Predecible = navegable.
2. **`domain` sin imports de framework**: no `fastapi`, no `sqlalchemy`, no `httpx`.
   Se verifica automáticamente con linters de arquitectura (import-linter).
3. **`shared_kernel` es pequeño y estable**: solo lo que de verdad comparten
   todos. Ante la duda, **duplicar** antes que acoplar (regla de DDD).
4. **`platform/` no contiene negocio**: solo *plumbing*. Si un módulo necesita
   lógica, va en su propio `domain`/`application`.
5. Cada módulo posee su **propio esquema** en PostgreSQL (`iam.*`, `billing.*`…).
   Prohibido `JOIN` entre esquemas de módulos distintos (ver [03](03-dependencias-entre-modulos.md)).

---

## 4. Convención de un servicio extraído

Cuando un módulo se promueve a microservicio, mantiene su estructura interna y
solo se le añade un *shell* de servicio:

```
services/execution/
├── src/elyon_execution/       # domain/application/infrastructure/interfaces
├── contracts/                 # su porción de OpenAPI/AsyncAPI
├── Dockerfile
├── helm/                      # chart propio
└── pyproject.toml
```

El **dominio no cambia**; solo el *transport* del contrato (in-process → gRPC/eventos).
Este es el retorno de invertir en fronteras desde el inicio.

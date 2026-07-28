# 04 — Stack Tecnológico

> Filosofía: **tecnología aburrida y probada en el núcleo**, innovación donde da
> ventaja real (ejecución, datos, cómputo). Cada elección viene con su porqué.
> Las decisiones formales viven como ADRs en [`docs/adr/`](../adr/).

## 1. Lenguajes

| Dominio | Lenguaje | Por qué |
|---------|----------|---------|
| Plataforma transaccional (monolito) | **Python 3.12** | Ecosistema quant/ML de primera clase, velocidad de desarrollo, tipado gradual maduro. Afinidad total con el dominio. |
| Cómputo cuantitativo (backtest/ML) | **Python 3.12** (NumPy, pandas/Polars, vectorbt/backtrader) | Es la *lingua franca* del quant. |
| Gateway de ejecución (hot path) | **Rust** | Sub-milisegundo, sin GC, seguridad de memoria, ideal para FIX/latencia. |
| Frontend | **TypeScript** | Tipado, ecosistema React, tipos compartidos con la API. |
| Infraestructura | **HCL (Terraform)** + **YAML (Helm/K8s)** | Estándar de industria. |

> **Alternativa considerada**: monolito en TypeScript/NestJS (excelente DI y
> módulos). Se elige Python por la afinidad con el dominio cuant y por poder
> compartir modelos entre backtesting, ejecución y ML. Ver `ADR-0003`.

## 2. Frameworks y librerías clave

### Backend (Python)
- **FastAPI** — API REST/WS, async, tipado, OpenAPI automático.
- **Pydantic v2** — validación y DTOs.
- **SQLAlchemy 2.0** (core + ORM) — persistencia; **Alembic** para migraciones.
- **dependency-injector** o wiring propio — Inversión de control explícita.
- **httpx** — cliente HTTP async (ACLs).
- **Temporal (SDK Python)** — workflows durables y sagas.
- **structlog / OpenTelemetry** — logging estructurado y tracing.

### Ejecución (Rust)
- **Tokio** — runtime async.
- **tonic** (gRPC) / cliente FIX propio.
- **rdkafka** — consumo/publicación en el bus.

### Frontend
- **Next.js 14 (App Router) + React 18** — SSR/SSG, streaming UI.
- **TanStack Query** — estado de servidor; **Zustand** — estado local.
- **Tailwind + Radix/shadcn** — design system.
- **TradingView Lightweight Charts** — gráficos financieros.
- **Vitest + Playwright** — tests unit/e2e.

## 3. Datos y persistencia

| Necesidad | Tecnología | Justificación |
|-----------|-----------|---------------|
| Datos transaccionales (OLTP) | **PostgreSQL 16** (schema por módulo) | ACID, madurez, RLS multi-tenant, JSONB. |
| Series temporales de mercado | **TimescaleDB** (o **ClickHouse** para analítica masiva) | Ingesta y consulta eficiente de ticks/OHLCV. |
| Analítica / agregaciones OLAP | **ClickHouse** | Consultas analíticas a gran escala. |
| Cache, locks, rate-limit, pub/sub efímero | **Redis 7** | Baja latencia, versátil. |
| Artefactos (tearsheets, parquet, backtests) | **S3 (object storage)** | Barato, durable, base del *data lake*. |
| Búsqueda (instrumentos, marketplace) | **OpenSearch** (fase 2) | Búsqueda y *faceting*. |
| Secretos | **Vault** / **AWS KMS + Secrets Manager** | Gestión y rotación de secretos. |

## 4. Mensajería y orquestación

| Componente | Tecnología | Uso |
|------------|-----------|-----|
| Event backbone | **Apache Kafka** (o **Redpanda** en fases tempranas) | Eventos de dominio, streaming de mercado. |
| Schema Registry | **Confluent/Apicurio** | Compatibilidad de esquemas de eventos. |
| Workflows / sagas | **Temporal** | Backtests, sagas de órdenes, procesos de larga duración. |
| Colas de tareas ligeras | **Redis + arq/Celery** | Jobs simples no críticos. |

> Redpanda vs Kafka: Redpanda (API-compatible, sin ZooKeeper) reduce coste
> operativo al principio; se puede migrar a Kafka gestionado (MSK) al escalar.

## 5. Infraestructura y plataforma

| Capa | Tecnología |
|------|-----------|
| Contenedores | **Docker** |
| Orquestación | **Kubernetes** (EKS/GKE) |
| IaC | **Terraform** (cloud) + **Helm/Kustomize** (workloads) |
| GitOps / CD | **ArgoCD** |
| CI | **GitHub Actions** |
| API Gateway / Ingress | **Envoy** / **Kong** / cloud API Gateway |
| Service Mesh (fase microservicios) | **Istio/Linkerd** (mTLS, tráfico) |
| CDN / Edge | **Cloudflare** |

## 6. Observabilidad y calidad

| Necesidad | Tecnología |
|-----------|-----------|
| Tracing distribuido | **OpenTelemetry → Tempo/Jaeger** |
| Métricas | **Prometheus → Grafana** |
| Logs | **Loki** (o ELK) |
| Errores | **Sentry** |
| Feature flags / config dinámica | **Unleash / OpenFeature** |
| Perfilado continuo | **Pyroscope** (fase 2) |

## 7. Seguridad (tooling)

- **Keycloak** (o Auth0) — OIDC/OAuth2, MFA, federación.
- **Trivy / Grype** — escaneo de imágenes y dependencias.
- **Semgrep / CodeQL** — SAST.
- **gitleaks** — detección de secretos.
- **OPA / Kyverno** — políticas en el clúster.
- **Sigstore/cosign** — firma de artefactos (supply chain).

## 8. Resumen del "por qué" (visión de CTO)

1. **Python en el núcleo** maximiza velocidad de un equipo pequeño y la afinidad
   con el dominio cuant, sin sacrificar mantenibilidad (tipado + arquitectura).
2. **Rust solo donde importa la latencia** — no se sobre-optimiza el resto.
3. **PostgreSQL + Timescale/ClickHouse** cubre OLTP y series temporales sin
   introducir cinco bases de datos exóticas el día 1.
4. **Kafka/Temporal** dan la columna vertebral event-driven y la fiabilidad de
   procesos que un sistema con dinero real exige.
5. **Kubernetes + GitOps** desde temprano porque el coste de migrar después es
   mucho mayor que el de adoptarlo con disciplina ahora.

Toda adopción de nueva tecnología pasa por un **ADR** con contexto, opciones,
decisión y consecuencias.

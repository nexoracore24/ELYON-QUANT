# 10 — Deployment Strategy

> Principio: **despliegues frecuentes, pequeños y reversibles**. El despliegue es
> un no-evento aburrido, gobernado por Git, no por humanos ejecutando comandos.

## 1. Entornos

| Entorno | Propósito | Datos | Despliegue |
|---------|-----------|-------|-----------|
| **local** | Desarrollo | `docker-compose` (todo el stack) | manual |
| **preview / PR** | Validar un PR aislado | efímeros | automático por PR (opcional) |
| **staging** | Réplica de prod, pre-release | sintéticos/anonimizados | automático desde `main` |
| **production** | Clientes reales | reales | promoción controlada |

`local` levanta con un comando (`make up`): Postgres, Timescale, Redis, Kafka
(Redpanda), Temporal, y todos los servicios. Paridad dev/prod alta.

## 2. Pipeline CI/CD

```
 push/PR ──► CI (GitHub Actions)                     ──► CD (ArgoCD / GitOps)
            ├─ lint, type-check, arch-linters             ├─ staging (auto)
            ├─ tests (unit/integ/contract)                ├─ smoke + e2e
            ├─ security (SAST, deps, secrets)             ├─ prod (progresivo,
            ├─ build imagen + SBOM + firma (cosign)       │   con aprobación)
            └─ push a registry (inmutable, por SHA)       └─ rollback automático
```

- **CI** (por PR y por merge): calidad + build de artefacto inmutable
  etiquetado por commit SHA, con **SBOM** y **firma** (supply chain).
- **CD** (GitOps): ArgoCD reconcilia el clúster con el estado declarado en Git.
  El deploy = un commit al repo de manifiestos (Helm/Kustomize). Auditable y
  reversible con `git revert`.

## 3. Estrategias de despliegue progresivo

- **Monolito `platform-api`**: **rolling update** con *readiness*/*liveness*
  probes + **canary** (5 % → 25 % → 100 %) vigilando SLIs. *Rollback* automático
  si se degradan métricas o suben errores.
- **`execution-gateway`** (crítico): **blue/green** para conmutación instantánea
  y *drain* ordenado de órdenes en vuelo; nunca se interrumpe una orden viva.
- **Cambios de esquema de BD**: **expand/contract** (migraciones compatibles
  hacia atrás) para desacoplar deploy de migración → *zero-downtime*.
  Nunca un cambio destructivo en el mismo deploy que lo empieza a usar.
- **Feature flags** para activar funcionalidad independientemente del deploy y
  hacer *dark launches* / *kill* sin redeploy.

## 4. Contenedores e infraestructura

- **Imágenes** mínimas (distroless/slim), multi-stage, no-root, *read-only fs*.
- **Kubernetes**: cada servicio con `requests/limits`, HPA (autoscaling por CPU/
  memoria/latencia o métricas custom), PodDisruptionBudgets, anti-affinity.
- **IaC**: Terraform para infra cloud (VPC, EKS, RDS, MSK, S3, IAM); Helm/
  Kustomize para workloads. Nada se crea "a mano" en la consola cloud.
- **Namespaces** por entorno/dominio; NetworkPolicies restrictivas por defecto.

## 5. Configuración y secretos en deploy

- Config por entorno vía `ConfigMap`/variables; **secretos** vía
  Vault/External-Secrets → nunca en imágenes ni en Git.
- Imágenes **inmutables**: la misma imagen promociona de staging a prod (se
  configura, no se re-construye). "Build once, deploy anywhere".

## 6. Migraciones de datos

- Ejecutadas como *job* controlado (pre/post-deploy hooks), idempotentes y
  reversibles cuando es posible.
- Estrategia **expand/contract** obligatoria para *zero-downtime*.
- Datos grandes (market data): *backfills* como jobs en `quant-workers`, no en
  el path de deploy.

## 7. Observabilidad del despliegue

- Cada release emite un *marker* (deploy annotation) en Grafana.
- **SLIs** vigilados durante el canary: tasa de error, latencia p99, saturación,
  y KPIs de negocio (órdenes rechazadas, fills). *Error budget* gobierna el
  ritmo de release.
- **Rollback** automático si se rompe el *budget* o disparan alertas.

## 8. Continuidad de negocio

- **Backups** automáticos (PITR en Postgres, snapshots), con **restore drills**
  periódicos (un backup no probado no existe).
- **RPO/RTO** definidos por criticidad: `execution`/`risk` los más estrictos.
- **Multi-AZ** desde el inicio; **multi-región** en Fase 4 (DR + residencia).
- **Kill-switch** global operativo desde el back-office para detener toda la
  ejecución en incidentes de mercado.

## 9. Runbooks y on-call

- `ops/runbooks/` con procedimientos por incidente (broker caído, lag de bus,
  BD saturada, activar kill-switch).
- On-call con *paging* (PagerDuty/Opsgenie); *post-mortems sin culpa* con
  acciones registradas.

## 10. Principios

1. **Todo por Git** (GitOps): el estado deseado es declarativo y auditable.
2. **Inmutable y firmado**: artefactos versionados, con SBOM y firma.
3. **Reversible por defecto**: si no sabes revertirlo, no lo despliegas.
4. **Zero-downtime**: expand/contract + rolling/canary/blue-green.
5. **El deploy no toca datos de clientes sin backup verificado.**

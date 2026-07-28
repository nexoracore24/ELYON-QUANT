# 08 — Git Strategy

> Objetivo: entrega continua con `main` **siempre desplegable**, cambios
> pequeños y revisados, e historial legible que sirva de auditoría.

## 1. Modelo de ramas: Trunk-Based Development

- **`main`** es la única rama de larga vida; siempre verde y desplegable.
- Ramas de trabajo **cortas** (horas o pocos días) salen de `main` y vuelven por
  PR. Nada de ramas `develop`/`release` de larga vida (evitamos GitFlow: es
  pesado para entrega continua).
- Trabajo grande y no terminado se integra tras **feature flags**, no en ramas
  eternas. Así evitamos *merge hell* y hacemos *trunk* real.

```
main  ──o───o────o────o────o────o──►  (siempre desplegable)
         \        \         \
          feat/... fix/...   chore/...   (ramas cortas, PR, squash)
```

### Nombres de rama
`<type>/<scope>-<descripcion-corta>` — p.ej.:
- `feat/execution-place-order`
- `fix/risk-leverage-check`
- `chore/ci-cache-deps`
- `docs/architecture-context-map`

`type` ∈ `feat|fix|chore|docs|refactor|test|perf|build|ci`.
`scope` = módulo o área (`execution`, `risk`, `web`, `infra`…).

## 2. Commits: Conventional Commits

Formato: `type(scope): summary` en imperativo, ≤72 chars.

```
feat(execution): add idempotent order placement
fix(risk): reject orders exceeding max leverage
refactor(portfolio): extract pnl calculation into domain service
docs(adr): add ADR-0003 primary backend language
```

- El cuerpo explica el **por qué** y consecuencias, no el *qué*.
- `BREAKING CHANGE:` en el footer cuando rompe contrato.
- Los tipos alimentan el **versionado semántico** y el *changelog* automático.

## 3. Pull Requests

- **Pequeños y enfocados** (idealmente < 400 líneas de diff). PR grande = señal
  de que faltó dividir.
- Plantilla obligatoria (`.github/pull_request_template.md`): contexto, cambios,
  cómo se probó, riesgos, checklist.
- **Mínimo 1 revisor** (2 para módulos *core*: `execution`, `risk`,
  `execution-gateway`). **CODEOWNERS** asigna revisores por carpeta/módulo.
- **Merge = squash** (un commit limpio por PR en `main`). Historial lineal.
- No se fusiona con CI en rojo ni con conversaciones sin resolver.
- Prohibido *force-push* a `main`; ramas protegidas.

## 4. Reglas de protección de `main`

- PR obligatorio (no *push* directo).
- Checks requeridos verdes: lint, type-check, tests, linters de arquitectura,
  escaneo de seguridad, build.
- Revisión aprobada + CODEOWNERS satisfechos.
- Historial lineal; firmas de commit verificadas (GPG/Sigstore).

## 5. Versionado y releases

- **SemVer** (`MAJOR.MINOR.PATCH`) por artefacto desplegable.
- **Tags** `v1.4.0`; releases derivadas automáticamente de conventional commits.
- **Changelog** generado (`release-please` o similar).
- Contratos (`/contracts`) versionan aparte y garantizan **compatibilidad hacia
  atrás** (ver [testing de contratos](09-testing-strategy.md)).
- Monorepo: releases **independientes por servicio/paquete** (versionado por
  ruta afectada), no un único número global.

## 6. Relación con despliegue

- Cada *merge* a `main` produce artefactos versionados e inmutables (imágenes
  firmadas). El despliegue lo gobierna **GitOps/ArgoCD** (ver
  [Deployment](10-deployment-strategy.md)), no un push manual.
- *Hotfixes*: rama `fix/...` desde `main`, PR expedito, misma barra de calidad.

## 7. Higiene

- Ramas fusionadas se borran automáticamente.
- Rebase (no merge) para actualizar una rama de trabajo con `main` y mantener
  historial limpio antes del squash.
- `.gitignore` estricto; **nunca** commitear secretos, `.env`, artefactos de
  build ni datos de mercado. `gitleaks` en pre-commit y CI.

## 8. Convención de esta iniciativa

El diseño de arquitectura vive en la rama `claude/elyon-quant-architecture-io2kdl`
y se integra por PR a `main` como cualquier otro cambio, respetando todo lo
anterior.

# 09 — Testing Strategy

> En un sistema que mueve dinero real, los tests no son opcionales: son el
> mecanismo que nos deja **desplegar rápido sin miedo**. Testeamos comportamiento
> y contratos, no implementación.

## 1. Pirámide de tests

```
                 ▲   E2E / Sistema         (pocos, críticos de negocio)
                /  \  ─────────────────────────────────
               /    \  Contract tests      (entre módulos y con brokers)
              /      \ ────────────────────────────────
             /        \ Integración        (repos, bus, adaptadores)
            /          \───────────────────────────────
           /____________\ Unit / Dominio   (muchísimos, rapidísimos)
```

- **Base ancha de tests de dominio**: rápidos, sin I/O, deterministas. El
  dominio es puro → se testea sin base de datos ni frameworks. Aquí vive la
  mayor densidad de aserciones.
- Cuanto más arriba, **menos tests y más caros**; se reservan para flujos que
  de verdad importan.

## 2. Niveles y alcance

| Nivel | Qué prueba | Herramientas | Dónde |
|-------|-----------|--------------|-------|
| **Unit / Domain** | Reglas de negocio, agregados, VOs, servicios de dominio | `pytest`, `hypothesis` (property-based) | por módulo |
| **Application** | Casos de uso (handlers) con *ports* en *fakes* | `pytest`, dobles de test | por módulo |
| **Integración** | Repos reales, migraciones, bus, ACLs contra dependencia real | `pytest` + **Testcontainers** (Postgres, Kafka, Redis) | por módulo |
| **Contract** | Compatibilidad de API y eventos entre productores/consumidores | **Pact** / Schema Registry compat / `schemathesis` (OpenAPI) | `/contracts` + CI |
| **E2E** | Flujos de negocio completos punta a punta | **Playwright** (UI), API-driven | `tests/e2e` |
| **Load / Performance** | Throughput, latencia p50/p95/p99, SLOs | **k6** / **Locust** | `tests/load` |
| **Chaos / Resiliencia** | Comportamiento ante fallos (broker caído, lag de bus) | experimentos controlados | `tests/chaos` |
| **Security** | SAST, deps, secretos, DAST | Semgrep/CodeQL, Trivy, gitleaks, ZAP | CI |

## 3. Reglas por capa (alineadas con Clean Architecture)

- **`domain`**: 100 % testeable sin *mocks* de infraestructura. Si necesitas un
  mock aquí, el diseño está mal.
- **`application`**: se testea con *fakes* de los *ports* (repos en memoria,
  bus en memoria). Verifica orquestación, no detalles técnicos.
- **`infrastructure`**: tests de **integración** con la dependencia real vía
  Testcontainers (nada de mockear el driver de la BD).
- **`interfaces`**: tests de contrato de API (OpenAPI) y de serialización de
  eventos.

## 4. Contract testing (crítico en modular monolith → microservicios)

Es lo que nos permite extraer servicios sin romper a los consumidores.

- **API HTTP**: contratos OpenAPI verificados con `schemathesis`; consumidores y
  productores comparten el contrato de `/contracts`. **Pact** para pares
  consumidor-productor cuando aplique.
- **Eventos**: Schema Registry con política de compatibilidad **BACKWARD**; un
  cambio incompatible **rompe el build**.
- **Brokers/exchanges**: contratos grabados (VCR/`respx`) + un *smoke test*
  periódico contra *sandbox* real del broker (MT5/IB/Binance testnet).

## 5. Datos de test y determinismo

- **Builders / Object Mothers** para construir agregados válidos (en
  `packages/py/elyon-testing`).
- **Backtests deterministas**: semilla fija, reloj inyectable, datos de mercado
  *fixture*; un mismo backtest debe dar **exactamente** el mismo resultado
  (test de reproducibilidad al bit).
- Reloj y aleatoriedad **inyectados** como *ports* (`Clock`, `Rng`) — nunca
  `datetime.now()` ni `random` directos en el dominio.
- Sin dependencia de orden entre tests; aislamiento por transacción/rollback o
  contenedor efímero.

## 6. Cobertura y calidad

- Umbral de cobertura por módulo (p.ej. **≥ 85 %** en `domain`+`application`;
  módulos *core* `execution`/`risk` más alto). La cobertura es señal, no meta:
  se complementa con **mutation testing** (`mutmut`/`cosmic-ray`) en el dominio
  crítico para verificar que los tests *matan* mutantes.
- CI falla si la cobertura del código cambiado baja del umbral.
- Tests *flaky* se cuarentenan y arreglan; cero tolerancia a rojos intermitentes.

## 7. Testing no-funcional

- **Performance/SLO**: k6 en pipeline nocturno contra staging; alertas si p99 o
  throughput degradan respecto al baseline.
- **Resiliencia**: experimentos de caos (matar pods, inyectar latencia/errores
  en ACLs de broker, lag de Kafka) validando *timeouts*, *retries*, *circuit
  breakers*, DLQs y *kill-switch*.
- **Seguridad**: SAST y escaneo de dependencias/secretos en cada PR; DAST
  periódico; pentest externo antes de hitos de escala.

## 8. Dónde corre cada cosa (CI)

| Etapa | En cada PR | Nocturno / pre-release |
|-------|-----------|------------------------|
| Unit + application | ✅ | ✅ |
| Integración (Testcontainers) | ✅ (afectados) | ✅ (todos) |
| Contract | ✅ | ✅ |
| E2E | *smoke* | ✅ completo |
| Load / Chaos | — | ✅ |
| Security (SAST/deps/secrets) | ✅ | ✅ + DAST |

## 9. Principio rector

**Testear a través de las fronteras públicas** (casos de uso, contratos), no de
los detalles internos. Así los tests sobreviven a los refactors y nos dan
libertad para evolucionar la implementación — que es justo lo que una plataforma
que aspira a durar décadas necesita.

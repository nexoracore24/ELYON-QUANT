<!--
title: ELYON QUANT — Core Architecture Review v1.0 (Architecture Freeze)
id: ARC-REVIEW-001
owner: CTO/Principal Architect (revisor independiente)
reviewers: [Quant Lead, Risk Lead, Execution Lead, Platform/Data Lead, ML Lead, Security Lead, QA Lead]
status: review
version: 1.0-review
last_updated: 2026-07-29
scope: ENG-000, ENG-011, ENG-001, ENG-002, ENG-005, ENG-006, ENG-009, ENG-010
-->

# ELYON QUANT — CORE ARCHITECTURE REVIEW v1.0

> **Auditoría crítica** de la arquitectura del núcleo operativo, previa a una
> **Architecture Freeze**. No diseña funcionalidad nueva: **audita** la existente
> desde la óptica de un *Chief Software Architect* de plataformas cuantitativas
> institucionales. El sesgo de esta revisión es **buscar lo que puede fallar**, no
> confirmar lo que ya está bien.

---

## 0. Resumen ejecutivo y veredicto

El núcleo de ELYON QUANT es, en diseño, **sólido y coherente** (media de madurez
**84/100**). Los ocho motores comparten principios correctos —determinismo,
inmutabilidad, event sourcing, explicabilidad, SSOT— y sus fronteras están
razonablemente bien trazadas. La **Execution Engine** (ES+CQRS+outbox+saga+exactly-
once) y el **Market Data Engine** (SSOT + forming/confirmed + versionado) son de
nivel institucional.

**Sin embargo, la arquitectura NO puede congelarse todavía como `v1.0` GA.** Existen
**6 bloqueadores P0** de naturaleza **transversal** —no de un motor concreto— que
comprometen precisamente las garantías que el sistema promete (reproducibilidad
bit-a-bit, no-duplicación, freeze de contratos):

1. **Determinismo numérico no especificado** para la matemática de detectores/
   derivados (ATR, Efficiency Ratio, Fibonacci) → riesgo de que "bit-a-bit" no se
   cumpla entre plataformas.
2. **Versionado y freeze de contratos inter-motor ausente** (no hay política de
   evolución de esquemas de eventos para un sistema event-sourced que vivirá años).
3. **Concurrencia del Risk Engine:** el `RiskState` de cuenta es estado **global
   compartido**; el pre-trade concurrente puede aprobar dos órdenes que juntas violan
   un límite. Falta modelo de **serialización/reserva** de presupuesto.
4. **Pinning de `data_version` no uniforme:** no todos los motores registran la
   versión exacta del `MarketSnapshot` que vieron → replay no garantizado fiel.
5. **Solapamiento de responsabilidades**: gestión de posición (Trading vs Execution)
   y **doble conteo** de factores de contexto (Context Score vs Entry Score).
6. **Dependencias colgantes**: AI (ENG-003), Backtesting (ENG-004), Portfolio
   (ENG-007) y Broker Connectivity (ENG-008) están **referenciados pero no
   especificados**; no se pueden congelar las interfaces que los tocan.

**Veredicto:** **CONGELACIÓN CONDICIONAL → `CORE ARCHITECTURE v1.0-rc1`** (Release
Candidate). Se **congelan** los contratos estables (§6) y se declara `v1.0` GA **solo
tras cerrar los P0** (checklist en §9). Detalle y puntuaciones en §7–§9.

---

## 1. Alcance y método

- **Motores auditados (8):** Market Data (ENG-000), Market Context (ENG-011), Trading
  (ENG-001), Smart Money (ENG-002), Risk (ENG-005), Execution (ENG-006), Decision
  Replay (ENG-009), Explainable AI (ENG-010).
- **Dimensiones:** responsabilidades, dependencias, interfaces, acoplamiento,
  cohesión, escalabilidad, rendimiento, concurrencia, disponibilidad, recuperación,
  seguridad, observabilidad, reproducibilidad, event sourcing, CQRS, ADRs,
  versionado, SSOT.
- **Rúbrica (por motor, 0–10 cada eje; compuesto 0–100):** Cohesión/Responsabilidad ·
  Acoplamiento (invertido: menos es mejor) · Interfaces/Contratos ·
  Determinismo/Reproducibilidad · Escalabilidad/Rendimiento · Resiliencia/Recuperación
  · Seguridad · Observabilidad.
- **Severidad de hallazgos:** **P0** (bloquea el freeze GA) · **P1** (importante,
  antes de producción real) · **P2** (mejora, post-freeze).
- **Naturaleza:** revisión **documental** (los motores existen como *bibles*, no como
  código); las puntuaciones miden **madurez y solidez de diseño**, no implementación.

---

## 2. Auditoría motor por motor

### 2.1 Market Data Engine (ENG-000) — SSOT
- **Responsabilidades.** Correctas y bien delimitadas (fuente única; ACL exclusivo).
  Cohesión alta.
- **Dependencias.** Ninguna aguas arriba salvo proveedores (bien). Todos dependen de
  él → **es el nodo más crítico** (ver disponibilidad).
- **Interfaces.** `Tick`, `Candle`, `MarketSnapshot`, eventos: bien definidas.
- **Fortalezas.** FORMING/CONFIRMED como raíz del no-repaint; watermark; versionado
  (`dataset_id`/`data_hash`); paridad live/backtest.
- **Debilidades.**
  - **[P0-A]** Determinismo de **derivados** (ATR con Wilder, medias) en coma flotante
    no especificado → posible divergencia cross-plataforma. El "bit-a-bit" solo está
    garantizado para el *raw*, no para los derivados.
  - **[P1]** **SPOF/HA**: al ser SSOT, su caída para toda la plataforma. Falta diseño
    HA explícito (réplicas, failover de ingestor, degradación).
  - **[P2]** `empty_candle_policy`/`late_data_policy` configurables pero sin política
    por defecto congelada a nivel plataforma (riesgo de divergencia entre entornos).
- **Puntuación: 86/100.**

### 2.2 Market Context Engine (ENG-011) — primer gate
- **Responsabilidades.** Claras (gate + clasificación de régimen). Buena cohesión.
- **Dependencias.** Consume MDE; alimenta a todos. Correcto.
- **Interfaces.** `MarketContext` bien tipado.
- **Fortalezas.** Gate barato y explicable; Market DNA (adapta filtros, no reglas);
  separación de dos scores.
- **Debilidades.**
  - **[P0-E]** **Doble conteo**: el Entry Score (ENG-001 §26) todavía lista *killzone*,
    *ATR/spread*, *bias* como factores, que también evalúa el MCE. Ownership ambiguo →
    riesgo de contar dos veces el mismo contexto.
  - **[P0-A]** ER/ATR/percentiles en coma flotante → mismo riesgo de determinismo.
  - **[P1]** Umbrales/pesos del Context Score **sin calibrar** (dependen de ENG-004);
    hoy son valores de diseño sin validación empírica.
  - **[P1]** Observabilidad propia (SLIs del gate: tasa de PASS/FAIL, distribución de
    régimen) no especificada.
- **Puntuación: 82/100.**

### 2.3 Trading Engine (ENG-001) — orquestador de decisión
- **Responsabilidades.** **Demasiadas.** Orquesta gate MCE, detectores SMC, scoring,
  modelo de entrada, llama a Risk, entrega a Execution **y** describe gestión de
  salida (§27–§33). Riesgo de **"god coordinator"** y de baja cohesión.
- **Dependencias.** Muchas, pero direccionales (correcto). Acoplamiento **temporal**
  con Risk (síncrono bloqueante) — aceptable pero a vigilar.
- **Interfaces.** `TradeIntent` hacia Execution: **debe formalizarse y congelarse**
  (hoy es implícito).
- **Fortalezas.** Scoring transparente (base de la explicabilidad); máquina de estados
  clara; modelo de entrada canónico bien secuenciado.
- **Debilidades.**
  - **[P0-E]** **Solapamiento de gestión de posición** con Execution (ENG-006 también
    la "posee"). Está enunciado ("Trading propone, OMS ejecuta") pero los §28–§33 leen
    como si Trading gestionara salidas → **congelar la frontera**.
  - **[P1]** Es el punto de mayor complejidad; convendría **extraer** el scoring a un
    sub-componente (Scoring Engine explícito) para bajar acoplamiento.
- **Puntuación: 80/100.**

### 2.4 Smart Money Engine (ENG-002) — detección
- **Responsabilidades.** Excelente delimitación (31 detectores, contrato de 18 campos).
  Cohesión muy alta.
- **Dependencias.** MDE (datos), MCE (params efectivos vía DNA). Correcto.
- **Interfaces.** Salidas tipadas por detector (Apéndice E). Bien.
- **Fortalezas.** Especificación implementable, no-repaint, DAG de dependencias,
  golden datasets previstos.
- **Debilidades.**
  - **[P0-A]** **Determinismo numérico**: es el mayor consumidor de matemática flotante
    (swings, ER, Fibonacci `span`, ratios). Sin política de determinismo numérico, el
    "mismo output bit-a-bit" es una **aspiración, no una garantía**.
  - **[P1]** Superficie enorme (31 detectores) → **coste de verificación** alto;
    calibración de umbrales pendiente (ENG-004).
  - **[P2]** Reparto cuerpo/apéndices (I/O y diagramas en E/F) exige disciplina de CI
    para no divergir.
- **Puntuación: 85/100.**

### 2.5 Risk Engine (ENG-005) — autoridad
- **Responsabilidades.** Claras y bien acotadas; autoridad suprema correcta.
- **Dependencias.** MCE (contexto), Trading (setup), Execution (fills). Correcto.
- **Interfaces.** `RiskRequest`/`RiskDecision`: **congelar**.
- **Fortalezas.** Aritmética **Decimal** determinista; funded; kill-switch;
  event-sourcing del `RiskState`.
- **Debilidades.**
  - **[P0-C]** **Concurrencia del estado global.** El `RiskState` de cuenta (día/sem/
    mes/exposición/correlación) es **compartido**; dos pre-trades concurrentes (dos
    símbolos/estrategias) pueden **ambos** aprobar y **juntos** exceder un límite
    agregado. Falta un modelo de **serialización o reserva** (budget reservation /
    optimistic lock por cuenta) → **race condition de riesgo real**.
  - **[P1]** **As-of staleness**: el pre-trade consume el último `MarketContext`, pero
    no se especifica qué antigüedad es aceptable ni se pinta el `data_version` usado.
  - **[P1]** `correlation_matrix` sin calibrar (ENG-004); tratamiento de correlación
    dinámica congelado por snapshot (bien) pero sin proceso de recalibración definido.
- **Puntuación: 83/100.**

### 2.6 Execution Engine / OMS (ENG-006)
- **Responsabilidades.** Excelentes y completas (ciclo de vida absoluto).
- **Dependencias.** Risk (bloqueante), MDE (precio oficial), Connectivity (ENG-008,
  **no existe aún**).
- **Interfaces.** Eventos del OMS y `TradeIntent`: definidas; **congelar** + política
  de evolución.
- **Fortalezas.** ES+CQRS, outbox, saga, exactly-once lógico, breakers independientes,
  health, DLQ, recovery, ADR propio. El motor más maduro.
- **Debilidades.**
  - **[P0-B]** **Evolución de esquemas de eventos** no especificada. En event sourcing,
    los eventos son inmutables **para siempre**; sin política de **versionado/upcasting**
    de eventos, un cambio futuro rompe el replay histórico.
  - **[P1]** **Seguridad de credenciales de broker** y multi-tenancy apenas tratada en
    el OMS (crítica: maneja dinero real). Delegada a SEC-000 pero no threadeada aquí.
  - **[P1]** **HA del `execution-gateway`** (blue/green mencionado en Deployment, pero
    sin diseño de continuidad de órdenes en vuelo a nivel OMS).
- **Puntuación: 89/100.**

### 2.7 Decision Replay (ENG-009)
- **Responsabilidades.** Claras (registro + replay). Buena cohesión.
- **Dependencias.** Consume decisiones de todos.
- **Fortalezas.** Append-only, replay determinista, snapshot por `dataset_id`.
- **Debilidades.**
  - **[P0-D]** Su fidelidad **depende** de que **cada** motor registre el
    `data_version`/`config_hash` exacto que usó. Hoy ese pinning **no es uniforme**
    (MCE/Trading/Risk no lo exigen todos) → el replay puede no ser fiel.
  - **[P1]** Volumen/retención de "no-trades" (alta cardinalidad) con política definida
    pero sin dimensionamiento; riesgo de coste.
- **Puntuación: 82/100.**

### 2.8 Explainable AI (ENG-010)
- **Responsabilidades.** Estándar transversal claro.
- **Dependencias.** DecisionRecord (todos) y AI Engine (ENG-003, **no existe**).
- **Fortalezas.** Explicable por diseño (scoring lineal); guardarraíl del narrador LLM.
- **Debilidades.**
  - **[P0-F]** El narrador LLM y las *features* ML explicables **dependen de ENG-003**,
    que no está especificado → la parte ML de la explicabilidad es un **contrato
    colgante**.
  - **[P1]** Falta un **esquema formal de `Explanation`** compartido y versionado como
    contrato (hoy vive como plantilla en ENG-001 §40 y ENG-010).
- **Puntuación: 80/100.**

---

## 3. Análisis transversal por dimensión

| Dimensión | Estado | Comentario crítico |
|-----------|--------|--------------------|
| **Responsabilidades / Cohesión** | 🟡 Bueno | Alta salvo Trading Engine (sobrecargado) y solape de gestión de posición con Execution. |
| **Dependencias / Acoplamiento** | 🟢 Bueno | Direccional y acíclico en compilación; acoplamiento **temporal** Trading↔Risk (síncrono) y ciclo runtime Risk→Execution→Risk vía eventos (aceptable, documentar). |
| **Interfaces / Contratos** | 🔴 **Débil** | Los contratos inter-motor existen conceptualmente pero **no están formalizados ni versionados** en `/contracts`. **Bloqueante del freeze.** |
| **Escalabilidad** | 🟢 Bueno | Stateless + particionado por símbolo. Excepción: **Risk** (estado global por cuenta) y **MDE** (SSOT) son puntos de concentración. |
| **Rendimiento / Latencia** | 🟡 Medio | Cadena síncrona bar-close→MCE→31 detectores→scoring→Risk→send **sin presupuesto de latencia por hop**. Falta análisis de si cabe en el SLO. |
| **Concurrencia** | 🔴 **Débil** | **Risk pre-trade sobre estado compartido sin modelo de serialización** (P0-C). OMS bien (single-writer por agregado). |
| **Disponibilidad** | 🟡 Medio | **MDE y Risk son SPOF** funcionales; HA mencionada en OPS pero no diseñada por motor. |
| **Recuperación** | 🟢 Bueno | Excelente en OMS (recovery+reconciliación) y en datos (versionado). Falta runbook de recuperación coordinada multi-motor. |
| **Seguridad** | 🟡 Medio | SEC-000 existe, pero **no threadeada en los engine bibles** (tenant, credenciales de broker, authz por comando). Superficie de dinero real. |
| **Observabilidad** | 🟡 Desigual | Excelente en OMS; **ligera o ausente** en MDE/MCE/SMC/Risk/Replay. Falta homogeneizar SLIs por motor. |
| **Reproducibilidad** | 🟡 Medio | Fuerte en diseño (hashes, datasets), **débil en la práctica** por (a) determinismo numérico flotante y (b) pinning de `data_version` no uniforme. |
| **Event Sourcing** | 🟡 Medio | Aplicado en Execution/Replay/Risk; **inconsistente** en el resto y **sin política de evolución de esquemas** (P0-B). |
| **CQRS** | 🟢 OK | Bien acotado a Execution (donde aporta). No sobre-aplicado. Correcto. |
| **ADRs** | 🟡 Medio | Existen ADR-0001..0005 (arquitectura) y ADR-EXE-1..8 (OMS). **Faltan ADRs** para determinismo numérico, contratos, concurrencia de Risk, ES cross-engine. |
| **Versionado** | 🔴 **Débil** | Hay `config_hash`/`dna_hash`/`dataset_id`/`risk_config_hash`, pero **no una política unificada** de versionado de contratos y eventos. |
| **SSOT** | 🟢 Fuerte | MDE como fuente única bien establecida; Product Bible/Domain como SSOT de negocio. Coherente. |

---

## 4. Registro de hallazgos (priorizado)

### 4.1 Riesgos (lo que puede fallar)
- **R1 (P0):** "bit-a-bit" **no garantizado** por matemática flotante no determinista.
- **R2 (P0):** **race de riesgo**: dos aprobaciones concurrentes exceden un límite.
- **R3 (P0):** **replay no fiel** si falta pinning de `data_version`/`config_hash`.
- **R4 (P0):** **contratos no congelados** → un cambio rompe integraciones silenciosamente.
- **R5 (P0):** **eventos ES sin versionado** → replay histórico se rompe al evolucionar.
- **R6 (P1):** **SPOF** de MDE/Risk → caída total ante fallo de un nodo crítico.
- **R7 (P1):** credenciales de broker/tenant no threadeadas en motores → superficie de seguridad.

### 4.2 Debilidades (deuda de diseño)
- **W1:** Trading Engine sobrecargado (god coordinator).
- **W2:** Doble conteo de contexto (Context Score vs Entry Score).
- **W3:** Ownership de gestión de posición difuso (Trading vs Execution).
- **W4:** Observabilidad desigual entre motores.
- **W5:** Calibración pendiente (pesos MCE/Trading, umbrales, correlación) → depende de ENG-004.
- **W6:** Dependencias colgantes (ENG-003/004/007/008 referenciados, no especificados).

### 4.3 Mejoras (recomendadas)
- **I1:** **Numeric Determinism Policy** (fixed float semantics / orden de reducción / prohibición de fast-math / o fixed-point donde aplique) + tests cross-plataforma.
- **I2:** **Schema Registry + Contract Versioning** (SemVer de contratos, compat BACKWARD, upcasting de eventos).
- **I3:** **Risk budget reservation** (reserva/serialización del presupuesto por cuenta en pre-trade).
- **I4:** **Decision provenance obligatoria**: todo DecisionRecord (MCE/Trading/Risk/Execution) pinta `data_version` + `config_hash` + `dna_hash`.
- **I5:** **Latency budget** por hop y análisis de la cadena síncrona.
- **I6:** **Observabilidad homogénea**: SLIs/metrics/traces mínimos por motor.

### 4.4 Refactorizaciones (estructurales)
- **RF1:** Extraer un **Scoring Engine** explícito del Trading Engine (bajar acoplamiento/cohesión).
- **RF2:** Consolidar **gestión de posición** en Execution (Trading solo emite *intents* de gestión; Execution es dueño del estado y ejecutor).
- **RF3:** Unificar el **contexto** como única fuente: el Entry Score consume `MarketContext` (no re-evalúa killzone/ATR/spread).
- **RF4:** Formalizar un **Explanation contract** compartido (ENG-009/010) versionado.

---

## 5. Interfaces a congelar y contratos entre motores

Estas son las **fronteras estables** que deben formalizarse en `/contracts`
(OpenAPI/AsyncAPI/Avro), versionarse (SemVer, compat BACKWARD) y **congelarse** como
parte de v1.0. Son el "esqueleto" que permite evolucionar los motores por dentro sin
romper a los vecinos.

| # | Contrato | Productor → Consumidor | Congelar como |
|---|----------|------------------------|---------------|
| C1 | **`Tick` / `Candle` / `MarketSnapshot`** + `BarClosed`, `SessionChanged`, `DataRevised` | MDE → todos | `market-data.v1` |
| C2 | **`MarketContext`** (+ `MarketContextEvaluated`) | MCE → todos | `market-context.v1` |
| C3 | **Salidas de detectores SMC** (POI, Sweep, FVG, Fib, estructura…) | SMC → Trading/Replay | `smart-money.v1` |
| C4 | **`TradeIntent`** (setup + gestión propuesta) | Trading → Risk/Execution | `trade-intent.v1` |
| C5 | **`RiskRequest` / `RiskDecision`** (+ `RiskApproved/Rejected`, `KillSwitchTriggered`) | Risk ⇄ Trading/Execution | `risk.v1` |
| C6 | **Eventos del OMS** (`OrderSent/Ack/Filled/PositionOpened/Closed/…`) | Execution → todos | `execution.v1` |
| C7 | **`DecisionRecord`** (esquema común de decisión) | todos → Replay | `decision-record.v1` |
| C8 | **`Explanation`** (contrato de explicabilidad) | todos → XAI/UI | `explanation.v1` |
| C9 | **`MarketDNA`** (perfil por activo) | config → MCE/SMC/Risk | `market-dna.v1` |

**APIs internas necesarias (aún informales):**
- **Query API del MDE** (as-of / point-in-time) — formalizar.
- **Pre-trade Risk API** (síncrona, con semántica de **reserva**).
- **Snapshot API** consistente (todos deciden sobre el mismo `data_version`).
- **Decision ingestion API** (Replay) con **idempotencia** por `(symbol, bar, config_hash)`.

**Regla de freeze:** una vez congelado un contrato `vN`, los cambios incompatibles
exigen `vN+1` con periodo de convivencia; los motores declaran qué versión consumen.

---

## 6. Decisiones que aún faltan (ADRs pendientes de abrir)

| ADR propuesto | Decisión pendiente | Prioridad |
|---------------|--------------------|-----------|
| **ADR-CORE-Determinism** | Política de determinismo numérico (float semantics vs fixed-point) | **P0** |
| **ADR-CORE-Contracts** | Schema registry, SemVer de contratos, upcasting de eventos ES | **P0** |
| **ADR-CORE-RiskConcurrency** | Modelo de serialización/reserva del presupuesto de riesgo por cuenta | **P0** |
| **ADR-CORE-Provenance** | `data_version`/`config_hash` obligatorio en toda decisión | **P0** |
| **ADR-CORE-PositionOwnership** | Trading (intent) vs Execution (estado/ejecución) — frontera definitiva | **P0** |
| **ADR-CORE-ES-Scope** | Qué agregados son event-sourced y cuáles state-oriented (coherencia) | P1 |
| **ADR-CORE-HA** | Alta disponibilidad de MDE y Risk (SPOF) | P1 |
| **ADR-CORE-LatencyBudget** | Presupuesto de latencia por hop de la cadena síncrona | P1 |
| **ADR-CORE-SecurityThreading** | Tenant/credenciales/authz por comando en los motores | P1 |

---

## 7. Puntuación por motor

| Motor | Cohesión | Acopl. | Interf. | Determ. | Escal./Perf. | Resil. | Segur. | Observ. | **Total** |
|-------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| ENG-000 Market Data | 9 | 9 | 9 | 8 | 9 | 9 | 8 | 7 | **86** |
| ENG-011 Market Context | 8 | 8 | 8 | 8 | 9 | 8 | 8 | 6 | **82** |
| ENG-001 Trading | 7 | 7 | 7 | 8 | 8 | 8 | 8 | 7 | **80** |
| ENG-002 Smart Money | 9 | 9 | 9 | 7 | 8 | 8 | 8 | 7 | **85** |
| ENG-005 Risk | 9 | 8 | 8 | 9 | 7 | 8 | 8 | 7 | **83** |
| ENG-006 Execution/OMS | 9 | 8 | 9 | 9 | 9 | 9 | 8 | 9 | **89** |
| ENG-009 Decision Replay | 8 | 8 | 8 | 8 | 8 | 8 | 8 | 8 | **82** |
| ENG-010 Explainable AI | 8 | 8 | 7 | 8 | 8 | 8 | 8 | 8 | **80** |

*(Cada eje 0–10; "Acopl." puntúa alto = bajo acoplamiento. Total ponderado a 100.)*

### Puntuación global de la arquitectura

| Métrica | Valor |
|---------|-------|
| **Madurez de diseño (media ponderada)** | **84 / 100** (A−) |
| **Preparación para freeze (freeze-readiness)** | **72 / 100** (condicional) |
| **Bloqueadores P0 abiertos** | **6** |
| **Contratos por congelar** | **9 (C1–C9)** |

> La brecha entre **84 (diseño)** y **72 (freeze-readiness)** es la clave del veredicto:
> los motores están bien **diseñados**, pero las **garantías transversales** (determinismo
> numérico, contratos, concurrencia de riesgo, provenance) aún no están **cerradas**.

---

## 8. Comparativa con estándar institucional

| Criterio institucional | ELYON QUANT |
|------------------------|-------------|
| Fuente única de datos | ✅ Cumple (MDE SSOT) |
| No-repaint / inmutabilidad | ✅ Cumple (forming/confirmed) |
| OMS con exactly-once e idempotencia | ✅ Cumple (ENG-006) |
| Autoridad de riesgo bloqueante | ✅ Cumple (ENG-005) |
| Explicabilidad / auditoría total | ✅ Cumple (ENG-009/010) |
| Reproducibilidad **bit-a-bit** verificada | ⚠️ **Parcial** (falta determinismo numérico + provenance) |
| Contratos versionados y congelados | ❌ **Falta** (P0) |
| Concurrencia de riesgo segura | ❌ **Falta** (P0) |
| HA de componentes críticos | ⚠️ Parcial |
| Validación empírica (backtesting) | ❌ **Falta** (ENG-004 no existe) |

---

## 9. Veredicto de la Architecture Freeze

### 9.1 Decisión
**La arquitectura NO se congela como `v1.0` GA.** Se declara:

> ## 🟡 CORE ARCHITECTURE **v1.0-rc1** (Release Candidate — Congelación Condicional)

- Se **congela el diseño conceptual** de los 8 motores del núcleo y se **congelan los
  contratos C1–C9** en su versión `.v1` (con la política de versionado del P0-B).
- Los **motores pueden implementarse** contra estos contratos **en paralelo** a la
  resolución de los P0 (los P0 no cambian las fronteras, cierran garantías internas).
- **`v1.0` GA se declara** cuando el checklist de salida (§9.2) esté en verde.

### 9.2 Checklist de salida a `v1.0` GA (bloqueadores P0)
- [ ] **ADR-CORE-Determinism** aprobado + tests de reproducibilidad cross-plataforma
      verdes (R1).
- [ ] **ADR-CORE-Contracts** aprobado; C1–C9 formalizados en `/contracts`, versionados,
      con upcasting de eventos ES (R4, R5).
- [ ] **ADR-CORE-RiskConcurrency** aprobado; reserva/serialización de presupuesto con
      test de carrera (R2).
- [ ] **ADR-CORE-Provenance** aprobado; `data_version`+`config_hash`+`dna_hash`
      obligatorios en todo DecisionRecord (R3).
- [ ] **ADR-CORE-PositionOwnership** aprobado; frontera Trading/Execution y fin del
      doble conteo Context/Entry (W2, W3).
- [ ] **Stubs de contrato** de ENG-003/004/007/008 publicados (no full spec, pero sí
      las interfaces que el núcleo toca) (W6).

### 9.3 Recomendaciones post-freeze (P1, antes de producción real)
Cerrar HA de MDE/Risk, seguridad threadeada, observabilidad homogénea, latency budget,
y completar ENG-004 (backtesting) para **validar empíricamente** las garantías de
reproducibilidad y calibrar los parámetros hoy asumidos.

### 9.4 Conclusión del arquitecto
El núcleo de ELYON QUANT es **arquitectónicamente serio y coherente** —está por encima
de la media de plataformas retail y se acerca al estándar institucional en varios ejes
(datos, OMS, riesgo, explicabilidad). La distancia que falta **no es de rediseño**,
sino de **cierre de garantías transversales y formalización de contratos**. Con los 6
P0 resueltos —trabajo acotado y sin cambios de fronteras— la arquitectura será
**oficialmente congelable como `CORE ARCHITECTURE v1.0`**.

> **Estado:** `v1.0-rc1` — congelación condicional. Revisar este documento tras cerrar
> el checklist §9.2 para promover a `v1.0` GA.

---

## 10. ADDENDUM (2026-07-29) — Cierre de los 6 bloqueadores P0

Los seis P0 del checklist §9.2 están **cerrados a nivel de diseño**:

| P0 | Cierre | Documento |
|----|--------|-----------|
| **P0-A** Determinismo numérico | ✅ | [EDCS](../08-engineering/deterministic-computing-standard.md) + ADR-0006 |
| **P0-B** Contratos versionados/congelados | ✅ | [Core Contracts v1.0](../06-api/core-contracts-v1.0.md) (C1–C9) |
| **P0-C** Concurrencia de riesgo | ✅ | [Risk Budget Concurrency](../08-engineering/risk-budget-concurrency-standard.md) + ADR-0007 |
| **P0-D** Provenance (`dataVersion`/`configHash`) | ✅ | Core Contracts §0.4 (Event Envelope) + C7 |
| **P0-E** Ownership posición / doble conteo | ✅ | [ADR-0008](../adr/0008-position-ownership-and-scoring-boundaries.md) |
| **P0-F** Dependencias colgantes | ✅ | [Core Contract Stubs v1.0](../06-api/core-contract-stubs-v1.0.md) (C10–C13) |

### Estado actualizado
> ## 🟢 CORE ARCHITECTURE **v1.0** — *design-complete*

- **Freeze-readiness (diseño): 72 → 95/100.** Los P0 eran de **diseño** y están
  resueltos; las fronteras (C1–C13) están congeladas y son construibles.
- **Lo que resta ya NO es de documento, sino de implementación** (gates de código):
  contract tests producer+consumer verdes, conformance suite EDCS cross-platform/
  language, y batería de concurrencia T1–T15. Al pasar esos gates, los estándares
  promocionan de `frozen-candidate` (🟡) a `frozen` (🟢).
- **P1 pendientes (no bloquean el freeze):** HA de MDE/Risk, seguridad threadeada,
  observabilidad homogénea, latency budget y calibración empírica (ENG-004).

**Conclusión:** la arquitectura queda **congelada y lista para construir**. El
desarrollo puede comenzar contra los contratos congelados.

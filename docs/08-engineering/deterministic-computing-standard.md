<!--
title: ELYON Deterministic Computing Standard (EDCS)
id: ENGX-EDCS-001 (estándar oficial de computación determinista; cierra P0-A)
owner: CTO/Principal Architect
reviewers: [Quant Lead, Risk Lead, Platform/Data Lead, ML Lead, Execution Lead, QA Lead]
status: frozen-candidate
version: 1.0
edcs_version: 1
last_updated: 2026-07-29
closes: Core Architecture Review v1.0 — P0-A (determinismo numérico)
-->

# ELYON DETERMINISTIC COMPUTING STANDARD (EDCS)

> **Estándar oficial de computación determinista de toda la plataforma.** Su
> objetivo: garantizar que **cualquier cálculo** produzca **exactamente el mismo
> resultado** con independencia del **sistema operativo, CPU, lenguaje, número de
> hilos o momento de ejecución**. Es de **cumplimiento obligatorio** para todos los
> motores (ENG-000..011). Cierra el bloqueador **P0-A** de la
> [Core Architecture Review v1.0](../architecture/core-architecture-review-v1.0.md).

Sin EDCS, las promesas de "reproducibilidad bit-a-bit", "no-repaint" y
"backtest≡live" del sistema son **aspiraciones, no garantías**. Este documento las
convierte en garantías verificables.

---

## 1. Filosofía del determinismo

1. **Determinismo = misma entrada ⇒ misma salida, siempre y en todas partes.** No es
   "casi igual": es **idéntico bit a bit** tras la serialización canónica.
2. **El no-determinismo es un bug, no una tolerancia.** Un cálculo que varía entre
   plataformas es un defecto que bloquea la aprobación de un motor.
3. **Exactitud sobre velocidad en el *hot path* de decisión.** El coste de un decimal
   exacto es aceptable; el coste de una decisión irreproducible no lo es.
4. **La frontera de cuantización manda.** Todo valor que **cruce un contrato, entre en
   un hash, se compare o dirija una decisión** debe estar en una **rejilla decimal
   canónica**. Las diferencias por debajo de la rejilla **no existen** para el sistema.
5. **Prohibido lo que el hardware/compilador puede reordenar.** FMA, fast-math,
   precisión extendida x87, reasociación SIMD, transcendentales de `libm`: fuentes de
   divergencia → **prohibidas** en el camino determinista.
6. **Reproducibilidad en tres ejes:** cross-**platform**, cross-**language**,
   cross-**version** (§ dedicados).

---

## 2. Reglas generales (invariantes ⛔)

- **G1.** Todo valor que dirige una decisión, se compara, se serializa o se hashea
  está en **Decimal canónico cuantizado** (§4, §5).
- **G2.** **Prohibida la coma flotante binaria** en el camino determinista de decisión
  (§3). Solo se admite en el *tier advisory* (§3.4) y sus salidas se **cuantizan**
  antes de usarse.
- **G3.** **Orden de evaluación fijo y documentado** por cálculo (§9). Nada depende del
  orden de iteración de un mapa/set ni del *scheduling* de hilos.
- **G4.** **Sin reloj de pared** en la lógica; el tiempo se inyecta (`Clock`) y es
  event-time (ENG-000). **Sin aleatoriedad** salvo con semilla fija y algoritmo
  determinista.
- **G5.** **Sin dependencias de locale** (formateo/parseo numérico, orden de
  colación): todo en formato canónico invariante (`.` decimal, sin separadores).
- **G6.** **Concurrencia sin efecto en el resultado:** el nº de hilos/procesos jamás
  cambia la salida (agregaciones con orden fijo, no reducciones paralelas no
  deterministas).
- **G7.** Todo cálculo declara: **inputs, orden, precisión de trabajo, escala de
  salida, redondeo y manejo de casos degenerados** (contrato de cálculo).
- **G8.** El comportamiento numérico se **versiona** (`edcs_version` + entra en
  `configHash`); cambiarlo es un **breaking change** (§11).

---

## 3. Tipos numéricos: permitidos y prohibidos

### 3.1 Permitidos
| Tipo | Uso |
|------|-----|
| **`Decimal` (IEEE 754-2008 `decimal128`)** | **Tipo canónico** de la plataforma para dinero, precio, tamaño, ATR, ratios, indicadores y umbrales. 34 dígitos significativos, contexto definido (§4.1). |
| **`int64` / `int32`** | Contadores, índices, `timestampNs`, cantidades enteras. Operaciones exactas (con control de overflow, §6). |
| **Fixed-point entero** (mantisa entera + escala fija) | Alternativa de bajo nivel/latencia equivalente a Decimal cuando se necesita rendimiento (p.ej. gateway); semánticamente idéntico a Decimal (§3.3). |

### 3.2 Prohibidos (en el camino determinista ⛔)
| Tipo/uso | Por qué |
|----------|---------|
| **`float32`/`float64` (IEEE754 binario) para valores de decisión** | Reasociación, FMA, x87, SIMD y transcendentales producen divergencia entre plataformas/compiladores. |
| **`double` como tipo de precios/dinero** | Errores de representación (0.1 no es exacto en binario) → drift acumulado. |
| **Números "nativos" de JSON** para decimales | JSON los interpreta como `double` → pérdida/ambigüedad (§10). |
| **Aritmética dependiente de FPU/registro** (x87 80-bit) | Precisión distinta según registro/plataforma. |

### 3.3 Fixed-Point
Representación entera con **escala fija** (`value = mantissa × 10^-scale`). Permitida
como implementación de bajo nivel **equivalente** a Decimal (mismo resultado tras
cuantización). Reglas: escala declarada por magnitud; operaciones definidas
(suma/resta con escalas alineadas; multiplicación reescala y **cuantiza con el
redondeo canónico**). Útil en el `execution-gateway` (Rust) por rendimiento; **debe**
pasar los mismos golden vectors que Decimal.

### 3.4 Floating-Point (uso restringido — *advisory tier*)
El único uso admitido de coma flotante binaria es el **cómputo interno advisory** que
**no** dirige por sí mismo una decisión determinista (p.ej. inferencia de un modelo ML
interno). Condiciones **obligatorias** si se usa:
- `binary64` (nunca `float32` para acumular), **round-to-nearest-even**, **sin FMA
  contraction**, **sin fast-math**, **sin x87** (forzar SSE2/64-bit), **sin
  reasociación SIMD**.
- **Sin transcendentales de `libm`** (`exp/log/pow/sin/...`) en resultados que se
  reutilicen; si son imprescindibles, usar la implementación canónica versionada (§8).
- **Cuantización obligatoria** de toda salida a la rejilla decimal **antes** de
  compararse, serializarse, hashearse o entrar en el scoring (frontera G1/G4).
- La no-reproducibilidad de la inferencia ML es un **riesgo conocido** (§13) y por eso
  su salida entra solo como **factor explicable cuantizado** (ENG-010), nunca como
  *override* opaco.

---

## 4. Política oficial de precisión

### 4.1 Contexto de trabajo (intermedios)
- **Contexto canónico = `decimal128` de IEEE 754-2008: 34 dígitos significativos,
  redondeo `ROUND_HALF_EVEN`.** Anclaje cross-language: Python `decimal` (`prec=34`),
  Java/Kotlin `MathContext.DECIMAL128`, Rust (lib decimal a 34 díg.), JS `decimal.js`
  configurado igual. **Todo intermedio** de un cálculo usa este contexto.
- El estado interno de acumuladores recursivos (EMA/Wilder) se mantiene a **precisión
  de trabajo completa** (no se cuantiza el estado; solo la salida) para evitar *drift*.

### 4.2 Escala de salida (cuantización canónica por magnitud)
| Magnitud | Escala de salida (decimales) | Fuente |
|----------|------------------------------|--------|
| Precio | `pricePrecision` del `instrumentProfile` (p.ej. EURUSD=5, XAUUSD=2) | Market DNA (C9) |
| ATR / distancias de precio | = `pricePrecision` (con guard-digits internos) | derivado de precio |
| Tamaño / lotes | `lotStep` (ROUND_DOWN, ENG-005) | Market DNA |
| Dinero / PnL | escala de la divisa (ISO-4217, p.ej. 2) | `money` |
| Ratios (ER, premium%, fill%) | **6 decimales** (fijo) | EDCS |
| Score | **entero** [0,100] | contratos |
| Niveles Fibonacci | `pricePrecision` | derivado de precio |

**Regla de cuantización (⛔):** un valor se cuantiza a su escala **solo en la salida**
(o en la frontera de contrato/hash/comparación), **una sola vez**, con el redondeo
canónico. Cuantizar en intermedios está prohibido (introduce error dependiente del
punto de cuantización).

---

## 5. Política de redondeo

- **Redondeo por defecto: `ROUND_HALF_EVEN`** (banker's) — insesgado, evita el sesgo
  acumulado de `HALF_UP` en estadísticos.
- **Excepciones documentadas (dominio):**
  - **Tamaño de posición → `ROUND_DOWN`** al `lotStep` (nunca arriesgar de más, ENG-005).
  - **Importe de riesgo al comparar contra un límite → `ROUND_UP`** (peor caso, ENG-005).
- **Ningún otro redondeo implícito.** Toda operación que reduzca escala declara su
  modo. Cambiar un modo de redondeo es un **breaking change** numérico (§11).
- **Sin `round()` nativo del lenguaje** (varían en el desempate); solo el redondeo
  decimal canónico configurado.

---

## 6. Aritmética: Decimal, entero e IEEE754

### 6.1 Decimal Arithmetic (canónica)
- Suma/resta/multiplicación: **exactas** en el contexto de trabajo hasta el límite de
  34 díg.; división y `sqrt`: **correctamente redondeadas** por el estándar decimal.
- `-0` se **normaliza a `0`**; `NaN`/`Infinity` **prohibidos** como valores válidos
  (un cálculo que los produzca es un error, no un resultado — §13).
- Comparaciones sobre valores **cuantizados** → **exactas** (§7).

### 6.2 Aritmética entera
- Exacta; **overflow prohibido**: usar `int64` con verificación (o enteros de precisión
  arbitraria para acumuladores de conteo grandes). El overflow silencioso es un bug ⛔.

### 6.3 IEEE754 (binario) — guarantías y trampas
- **Garantiza:** `+,-,*,/,sqrt` correctamente redondeados y **reproducibles** *si* se
  fija `binary64`, redondeo nearest-even y **orden** de operaciones.
- **NO garantiza (⛔ fuentes de divergencia):** asociatividad (`(a+b)+c ≠ a+(b+c)`);
  **FMA/contracción** (una mul-add fusionada da otro último bit); **x87 80-bit**;
  **fast-math**/`-ffast-math`; **reasociación/vectorización SIMD**; **transcendentales
  de `libm`** (no exigidos por IEEE754 → cada plataforma difiere en el último ULP);
  subnormales; `-0`; `NaN` payloads.
- **Conclusión:** el binario **no** se usa para valores de decisión (§3.2). Si aparece
  (advisory), se somete a §3.4.

---

## 7. Comparaciones y Epsilon Policy

- **Regla EDCS (⛔): comparar sobre valores Decimal cuantizados a su escala canónica →
  comparación EXACTA.** No se usan epsilons en el camino determinista: un epsilon
  reintroduce ambigüedad y dependencia de plataforma que la cuantización ya eliminó.
- **Empates en la frontera** (p.ej. score justo en el umbral): resueltos por el
  **redondeo canónico** de la cuantización + comparación exacta → resultado
  determinista y documentado (no "epsilon").
- **Epsilon — uso residual y acotado:** solo dentro del *advisory tier* (§3.4) sobre
  floats **antes** de cuantizar; el epsilon es **fijo, declarado y versionado** (no
  "un número pequeño cualquiera"). Fuera de ahí, **prohibido**.
- **Reconciliación:** esta política **refina** la mención de `epsilon` de la Risk Engine
  Bible (ENG-005 §0.2): en el camino canónico se compara **exacto sobre cuantizado**;
  el `epsilon` de ENG-005 se interpreta como la **escala de cuantización** (última
  cifra significativa), no como una tolerancia difusa.

---

## 8. Operaciones matemáticas: permitidas y restringidas

### 8.1 Permitidas (Decimal, deterministas)
`+`, `−`, `×`, `÷` (redondeo canónico), `abs`, `min`, `max`, `sqrt` (decimal,
correctamente redondeada), potencias de exponente **entero** (por multiplicación
repetida, orden fijo), cuantización canónica.

### 8.2 Restringidas / prohibidas en el camino de decisión (⛔)
- **Transcendentales** (`exp`, `ln`, `log`, `pow` no entera, `sin/cos/tan`): prohibidos
  vía `libm`. Si son **imprescindibles**, se usa la **implementación canónica
  versionada** (serie decimal a precisión fija con `HALF_EVEN`, con golden vectors y
  `edcs_version`). Se **prefiere reformular** para evitarlos (p.ej. retornos simples en
  vez de log-retornos, §8.7).
- **Divisiones por cero:** cada cálculo define su resultado en el caso degenerado (no
  se propaga `NaN/Inf`).
- **Funciones dependientes de plataforma** (`hypot`, `fma`, RNG del sistema): prohibidas.

> Todos los indicadores siguientes se computan en **contexto `decimal128`** (§4.1),
> con **orden fijo** (§9) y **cuantización solo en la salida** (§4.2).

### 8.3 ATR (Average True Range)
- `TR_i = max(H_i − L_i, |H_i − C_{i-1}|, |L_i − C_{i-1}|)` (Decimal, exacto).
- **Semilla (Wilder):** `ATR_n = mean(TR_1..TR_n)` = `(Σ_{i=1..n} TR_i) / n`, suma en
  **orden ascendente de índice**.
- **Recurrencia (Wilder):** `ATR_i = (ATR_{i-1} × (n−1) + TR_i) / n`, estado interno a
  precisión de trabajo completa.
- **Salida:** cuantizada a `pricePrecision`. **Degenerado:** `i < n` → `insufficient_data`.
- `atr_period` (n) es config → entra en `configHash`.

### 8.4 Fibonacci
- `span = dest − origin` (Decimal exacto; `origin/dest` son precios).
- **Retroceso:** `P_ret(r) = dest − r × span`. **Proyección:** `P_proj(e) = origin + e × span`.
- `r, e` son **literales decimales exactos** (`0.618`, `0.705`, `0.786`, `1.272`,
  `1.618`, `2.0`, `2.618`) — **nunca** floats (`0.618f` ≠ `0.618` exacto).
- **Salida:** niveles cuantizados a `pricePrecision`. **Degenerado:** `span = 0` →
  `no_fib` (ENG-002 D32).

### 8.5 Efficiency Ratio (ER)
- `ER = |C_t − C_{t−w}| / Σ_{i=t−w+1..t} |C_i − C_{i−1}|` (Decimal).
- **Denominador:** suma en **orden ascendente de índice**.
- **Degenerado (⛔):** denominador `= 0` (serie plana) → `ER = 0` (definido, no `NaN`).
- **Salida:** cuantizada a **6 decimales** (escala de ratio).

### 8.6 VWAP
- `VWAP = (Σ price_i × volume_i) / (Σ volume_i)` (Decimal, orden ascendente de índice).
- **Degenerado:** `Σ volume = 0` → `undefined` (se omite el punto; nunca `NaN`).
- **Salida:** cuantizada a `pricePrecision`.

### 8.7 Medias móviles y volatilidad
- **SMA:** `Σ x_i / n` (orden ascendente). **EMA/Wilder:** `EMA_i = α × x_i + (1−α) ×
  EMA_{i-1}`, con `α` **decimal exacto** (`α = 2/(n+1)` calculado en Decimal) y **semilla
  definida** (`EMA_n = SMA_n`); estado a precisión completa, salida cuantizada.
- **Volatilidad realizada:** `vol = sqrt( Σ (r_i − mean)^2 / d )`, con `d ∈ {n, n−1}`
  **declarado explícitamente** (población vs muestral), `sqrt` decimal, retornos
  **simples** `r_i = (C_i − C_{i-1})/C_{i-1}` por defecto (evita `ln`). Si se requieren
  log-retornos, `ln` canónico versionado (§8.2).
- **Salida:** ratios a 6 decimales; precios a `pricePrecision`.

### 8.8 Indicadores derivados (regla general)
**Cualquier** indicador derivado nuevo **debe** declarar su **Contrato de Cálculo**:
`inputs · orden de evaluación · precisión de trabajo (decimal128) · escala de salida ·
redondeo · casos degenerados · edcs_version`. Sin este contrato, no se aprueba (§14).

---

## 9. Orden obligatorio de evaluación

- **O1.** Toda operación es **secuencial e izquierda-a-derecha** según la fórmula
  documentada; **prohibida la reasociación** (aunque "matemáticamente" sea equivalente).
- **O2.** Las series temporales se recorren en **orden ascendente de `event_time`**
  (desempate por `seq`/índice estable). Nunca en orden de llegada ni de hash.
- **O3.** **Sin reducciones paralelas** salvo *tree-reduction* de **forma fija y
  documentada** (misma topología en todas partes). Por defecto: **suma secuencial**.
- **O4.** El resultado **no depende** del nº de hilos/particiones (G6).

## 10. Política de agregación

- **Sumas/medias:** orden **ascendente de índice**, contexto `decimal128`. Como Decimal
  es exacto hasta 34 díg., **no** se necesita Kahan; si una suma pudiera exceder 34
  díg. significativos, se usa acumulador de **precisión arbitraria** (exacto) y se
  cuantiza al final.
- **Conjuntos sin orden natural** (p.ej. pools de liquidez, factores): se ordenan por
  una **clave total definida** (§12.4) **antes** de agregar/hashear.
- **Min/Max:** deterministas; empates resueltos por clave secundaria definida.

## 11. Política de acumulación

- **Acumuladores recursivos** (EMA/Wilder/running sums): **estado a precisión de trabajo
  completa** (34 díg.), **inicialización definida** (semilla), **cuantización solo en la
  lectura**. El estado interno es **idéntico** en toda plataforma (mismo contexto) →
  sin *drift*.
- **Reproducibilidad del estado:** un acumulador debe poder **reconstruirse** desde la
  serie de entradas (mismo resultado que en vivo) — encaja con event-sourcing (ENG-006/
  ENG-009).
- **Cambiar** la semilla/recurrencia = **breaking numérico** → `edcs_version++` + recompute
  de datasets afectados (§12.7 cross-version).

## 12. Política de normalización, hashing, serialización e IDs

### 12.1 Normalización
- **Percentiles:** método **declarado y fijo** (por defecto *nearest-rank* con regla de
  interpolación lineal definida); mismo método en todo el sistema. **Degenerado:** n<2 →
  definido.
- **Min-max:** `(x−min)/(max−min)`; **rango degenerado** (`max==min`) → resultado
  definido (p.ej. `0.5` o `0`, declarado por uso). **Z-score:** media/desv. Decimal;
  `std==0` → definido.
- Toda normalización cuantiza a escala de ratio (6 dp) en la salida.

### 12.2 Deterministic Serialization / Canonical JSON
Forma canónica (base **RFC 8785 JCS** con endurecimiento decimal):
- **UTF-8**, sin espacios insignificantes.
- **Claves de objeto ordenadas** por punto de código Unicode (ascendente).
- **Decimales como *string* canónico** (⛔ nunca número JSON/`double`): sin ceros a la
  izquierda, punto `.`, **sin notación científica**, `-0`→`0`, escala **fija** de la
  magnitud (p.ej. `"1.08500"`).
- **Enteros** como número JSON sin ceros a la izquierda; `timestampNs` como `int64`.
- **Strings** con escapado mínimo canónico; **arrays** preservan orden (los conjuntos se
  ordenan por clave total, §12.4).
- **Booleanos/null** literales. La serialización es **idempotente** (serializar dos
  veces da el mismo byte-string).

### 12.3 Data Hashing / Config Hashing
- **`data_hash` / `config_hash` = SHA-256(canonical_serialization(objeto))**, hex
  minúsculas.
- **`data_hash`** (MDE): sobre los campos definidos de la vela/serie (orden y conjunto
  fijos); datasets = **Merkle root** sobre velas en orden ascendente de `open_time`.
- **`config_hash`**: sobre la **config efectiva completa** que afecta a la salida
  (params + pesos + umbrales + `edcs_version` + `dnaHash` + versiones de algoritmo),
  serialización canónica con claves ordenadas. **Si algo afecta el resultado y no está
  en el hash, es un bug ⛔.**

### 12.4 Canonical Ordering
- Colecciones sin orden intrínseco que se hashean/comparan se ordenan por una **clave
  total definida** (p.ej. pools por `(price, type, origin)`; factores por `factorId`).
- **Eventos** por `(event_time, producer, seq)`.
- El orden canónico es **estable** (determinista y sin empates irresolubles).

### 12.5 Stable IDs
- **IDs deterministas** (camino reproducible: `decisionId`, `clientOrderId` en backtest,
  ids derivados): `id = UUIDv5(namespace, canonical_key)` **o** `hash(canonical_key)`,
  donde `canonical_key` es la serialización canónica de las claves de negocio (p.ej.
  `decisionId = uuid5(NS_DECISION, {symbol, barCloseTime, configHash, decisionType})`).
  → mismas entradas ⇒ **mismo ID** (backtest≡replay).
- **IDs aleatorios** (`UUIDv7`, ordenable por tiempo): **solo** donde basta unicidad y
  no se exige reproducibilidad (p.ej. `eventId` de infraestructura). **Nunca** en el
  cálculo determinista.
- Prohibido derivar IDs de **`now()`** de pared o de contadores dependientes de proceso
  en el camino determinista.

---

## 13. Reproducibilidad en tres ejes

### 13.1 Cross-platform (SO / CPU / endianness)
- Decimal + serialización canónica **eliminan** la dependencia de FPU/endianness (los
  hashes se calculan sobre bytes canónicos de texto/big-endian, no sobre la
  representación en memoria).
- **Prohibidas** las operaciones platform-dependent (§6.3): FMA, x87, fast-math, SIMD
  reassociation, `libm`. CI ejecuta la suite en **≥2 arquitecturas** (x86-64 y ARM64) y
  **≥2 SO** y compara `data_hash`/golden vectors → deben coincidir.

### 13.2 Cross-language (Python / Rust / TypeScript)
- Los tres implementan **los mismos algoritmos** (esta norma) con el **mismo contexto
  decimal128** y la **misma serialización canónica**. Librerías: Python `decimal`,
  Rust decimal (a 34 díg.), JS `decimal.js` — **configuradas idénticas**.
- **Conformance suite compartida** (golden vectors en `/contracts` o `tests/`): cada
  lenguaje debe reproducir **byte a byte** los mismos resultados. Un lenguaje que no
  pase la suite **no** puede alojar cálculo determinista.

### 13.3 Cross-version (evolución en el tiempo)
- El comportamiento numérico está **versionado** (`edcs_version`, hoy `1`) y entra en
  `configHash`. Un cambio de algoritmo/semilla/redondeo/escala = **breaking numérico** →
  `edcs_version++`, ADR + RFC, y **recompute versionado** de los datasets afectados (los
  antiguos conservan su `edcs_version` y `configHash` → su historia sigue siendo
  reproducible con su versión).
- **Nunca** se cambia el comportamiento numérico "en caliente": una decisión histórica
  se reproduce **con la versión con la que se tomó** (encaja con upcasting de contratos,
  API-CORE-001 §1.3).

---

## 14. Casos de prueba (golden vectors deterministas)

- **T1 Asociatividad:** `(a+b)+c` vs orden canónico sobre serie conocida → el motor debe
  usar el **orden canónico** y coincidir con el golden.
- **T2 Redondeo half-even:** `2.5→2`, `3.5→4`, en la escala canónica.
- **T3 ATR:** serie fija de N+k velas → ATR esperado (golden) idéntico en Py/Rust/TS.
- **T4 Fibonacci:** `origin/dest` fijos → niveles `0.618/0.705/1.618` exactos (literales
  decimales, no float).
- **T5 ER denominador cero:** serie plana → `ER=0` (no `NaN`).
- **T6 VWAP volumen cero:** → `undefined` (no `NaN`).
- **T7 Volatilidad:** `d=n` vs `d=n−1` declarado → golden por cada uno.
- **T8 Canonical JSON:** dos objetos con claves en distinto orden de inserción →
  **misma** cadena canónica y **mismo** `data_hash`.
- **T9 Decimal-as-string:** `0.1+0.2 == 0.3` exacto (Decimal), y su serialización es
  `"0.3"` (no `"0.30000000000000004"`).
- **T10 Stable ID:** mismas claves de negocio → **mismo** `decisionId` (backtest≡replay);
  claves distintas → IDs distintos.
- **T11 Cross-platform:** misma suite en x86-64 y ARM64 → `data_hash` idénticos.
- **T12 Concurrencia:** agregación con 1 vs N hilos → resultado idéntico (G6).
- **T13 Config hash cobertura:** cambiar un parámetro que afecta la salida **cambia** el
  `config_hash`; cambiar un comentario **no** lo cambia.

## 15. Casos límite (edge cases)

Serie vacía / de 1 elemento / todos iguales / magnitudes extremas (muy grande + muy
pequeña) / negativos / `span=0` (Fibonacci) / rango degenerado (min==max en
normalización) / denominador cero (ER/VWAP/z-score) / precios en el límite de
`pricePrecision` / gaps de datos / primeras `n` velas (`insufficient_data`). **Cada
uno tiene un resultado definido y un golden vector** (nunca `NaN`/`Inf`/excepción no
controlada).

## 16. Riesgos conocidos

| # | Riesgo | Mitigación EDCS |
|---|--------|-----------------|
| K1 | FMA / fast-math / x87 / SIMD reassociation | Prohibidos; flags de compilación fijados; Decimal en el camino de decisión |
| K2 | Transcendentales `libm` divergen | Prohibidas; implementación canónica versionada o reformulación |
| K3 | Contexto decimal mal configurado entre lenguajes | `decimal128` fijo + conformance suite cross-language |
| K4 | JSON coacciona decimales a `double` | Decimales como **string** en canonical JSON |
| K5 | Hash de mapa/set con orden no determinista | Canonical ordering por clave total antes de hashear |
| K6 | IDs basados en `now()`/random en camino determinista | Stable IDs derivados (UUIDv5/hash de clave) |
| K7 | Inferencia ML no determinista (GPU/threads/BLAS) | *Advisory tier* + cuantización + factor explicable; documentado |
| K8 | Overflow entero silencioso | Verificación / precisión arbitraria |
| K9 | Locale (coma decimal, colación) | Formato/colación invariante |
| K10 | Concurrencia sobre estado compartido | Fuera de alcance numérico; ver Risk P0-C (concurrencia) |

## 17. Reglas obligatorias (resumen ⛔)

R-EDCS-1 Decimal canónico para todo valor de decisión/comparación/serialización/hash ·
R-EDCS-2 Float binario prohibido salvo advisory + cuantización · R-EDCS-3 Contexto
`decimal128` (34 díg., half-even) para intermedios · R-EDCS-4 Cuantización solo en la
salida, una vez · R-EDCS-5 Orden de evaluación fijo, sin reasociación · R-EDCS-6
Comparación exacta sobre cuantizado; epsilon prohibido en camino canónico · R-EDCS-7
Transcendentales `libm` prohibidos · R-EDCS-8 Casos degenerados definidos, nunca
`NaN/Inf` · R-EDCS-9 Canonical JSON + decimales-string + claves ordenadas · R-EDCS-10
Data/Config hashing SHA-256 sobre forma canónica; el `config_hash` cubre **todo** lo
que afecta la salida · R-EDCS-11 Stable IDs derivados en camino determinista · R-EDCS-12
Comportamiento numérico versionado (`edcs_version`); cambios = breaking + recompute.

## 18. ADRs relacionados

- **ADR-0006 · Deterministic Computing (EDCS)** — *decimal-first + frontera de
  cuantización + prohibición de float/transcendentales en el camino de decisión +
  serialización canónica*. Alternativas descartadas: (a) IEEE754 binario "controlado"
  (frágil: FMA/x87/SIMD/libm siguen divergiendo entre compiladores); (b) tolerancias por
  epsilon (reintroducen ambigüedad y dependencia de plataforma). Decisión: **Decimal
  canónico** con float solo advisory. (Se crea como ADR formal, ver `docs/adr/`.)
- **Relacionados:** ADR-CORE-Provenance (P0-D, ya cubierto por Core Contracts),
  ADR-0005 database-per-module, ENG-005 §0.2 (aritmética monetaria), API-CORE-001
  (contratos: decimales como string, envelope, upcasting).

---

## 19. CHECKLIST DE CONFORMIDAD EDCS (obligatorio para aprobar cualquier motor nuevo)

> Un motor **no puede aprobarse** (ni promover su bible a 🟢) sin marcar **todos** los
> ítems. QA verifica en el gate; se enlaza a los tests (BLD-003).

**Tipos y precisión**
- [ ] Todo valor de decisión/comparación/serialización/hash es **Decimal canónico**.
- [ ] **Cero** coma flotante binaria en el camino de decisión (o justificada como
      advisory §3.4 con cuantización y test).
- [ ] Intermedios en **`decimal128`** (34 díg., half-even); estado de acumuladores a
      precisión completa.
- [ ] Cuantización **solo en la salida**, a la escala canónica por magnitud (§4.2).

**Operaciones y orden**
- [ ] **Orden de evaluación fijo** y documentado por cálculo (Contrato de Cálculo, §8.8).
- [ ] Agregaciones en **orden ascendente de índice**; sin reducciones paralelas no
      deterministas; resultado independiente del nº de hilos.
- [ ] **Sin transcendentales `libm`**; potencias solo enteras o `sqrt` decimal / algoritmo
      canónico versionado.
- [ ] **Comparaciones exactas** sobre valores cuantizados; **sin epsilon** en camino
      canónico.
- [ ] **Todos los casos degenerados** definidos (denominador cero, series cortas,
      rango degenerado) → sin `NaN/Inf`.

**Serialización, hashing e IDs**
- [ ] Salidas y estado serializan a **Canonical JSON** (decimales-string, claves
      ordenadas, idempotente).
- [ ] `data_hash`/`config_hash` = SHA-256 canónico; el `config_hash` cubre **todo**
      lo que afecta la salida (incl. `edcs_version`, `dnaHash`).
- [ ] Colecciones sin orden natural se ordenan por **clave total** antes de hashear.
- [ ] IDs del camino determinista son **stable IDs** derivados (no `now()`/random).

**Reproducibilidad y versionado**
- [ ] **Golden vectors** por cálculo + **casos límite** (§14–§15) con resultado esperado.
- [ ] Suite de **conformidad cross-platform** (x86-64/ARM64) y **cross-language**
      (Py/Rust/TS) verde.
- [ ] Comportamiento numérico **versionado** (`edcs_version` en `configHash`); plan de
      **recompute** ante breaking numérico.
- [ ] **Sin reloj de pared / sin aleatoriedad** no sembrada en la lógica; tiempo por
      `Clock`/event-time.

---

> **Versión 1.0 — `frozen-candidate` (🟡).** Estándar oficial de computación determinista
> de ELYON QUANT. Cierra **P0-A**. De cumplimiento **obligatorio**: ningún motor se
> aprueba sin pasar el checklist §19. Promoción a `frozen` (🟢) con la conformance suite
> cross-platform/language verde. Cambios de comportamiento numérico → `edcs_version++`
> vía RFC/ADR (ADR-0006).

# 11 — Seguridad

> Manejamos credenciales de broker y capital real. La seguridad es un requisito
> de diseño de primer nivel, no una capa que se añade al final.
> **Secure by design, defense in depth, least privilege, zero trust.**

## 1. Modelo de amenazas (resumen)

Activos críticos: credenciales de broker/exchange, fondos y órdenes, datos
personales (PII), estrategias propietarias, claves y secretos. Adversarios:
atacante externo, tenant malicioso (multi-tenancy), insider, dependencia
comprometida (supply chain). Metodología: **STRIDE** por bounded context;
revisión de amenazas en cada cambio significativo (ver ADR/diseño).

## 2. Autenticación (AuthN)

- **OAuth2 / OIDC** vía Keycloak/Auth0. Tokens **JWT** de vida corta +
  *refresh tokens* rotatorios.
- **MFA obligatorio** para acciones sensibles (activar trading live, retirar,
  gestionar credenciales de broker).
- **API keys** por tenant con *scopes* limitados para acceso programático;
  rotación y revocación soportadas.
- Credenciales de broker **nunca** las maneja el frontend; se capturan cifradas
  y se almacenan en un *vault* (ver §5).

## 3. Autorización (AuthZ)

- **Multi-tenant**: todo recurso pertenece a un `tenant_id`; el contexto de
  tenant se resuelve en el *middleware* y se propaga. **Aislamiento reforzado
  con Row-Level Security (RLS)** en PostgreSQL — no confiamos solo en el filtro
  de la app.
- **RBAC + ABAC**: roles (owner, admin, trader, viewer) y políticas basadas en
  atributos (p.ej. "solo el owner puede subir el límite de riesgo").
- **Least privilege** por defecto: negar salvo permiso explícito.
- Autorización evaluada en la **capa de aplicación** (parte del caso de uso), no
  solo en el controlador.
- Políticas declarativas con **OPA** para reglas transversales.

## 4. Protección de datos

- **En tránsito**: TLS 1.3 en todo; **mTLS** entre servicios (service mesh en
  fase microservicios).
- **En reposo**: cifrado de BD, discos y backups (KMS).
- **PII**: minimización, cifrado a nivel de campo para datos sensibles,
  *tokenization* donde aplique; enmascarado en logs.
- **Residencia de datos**: soporte multi-región para requisitos jurisdiccionales
  (Fase 4).
- **Retención y borrado**: políticas de retención y *right-to-be-forgotten*
  (GDPR) — salvo lo que la ley financiera obligue a conservar (audit ledger).

## 5. Gestión de secretos

- **Vault** (o cloud Secrets Manager + KMS) como fuente única; **cero secretos**
  en código, imágenes o Git.
- Credenciales de broker cifradas con *envelope encryption*; acceso auditado y
  con *lease*/rotación.
- Detección de secretos (`gitleaks`) en pre-commit y CI; rotación ante fuga.
- Identidades de servicio de vida corta (IRSA/workload identity), no llaves
  estáticas de larga vida.

## 6. Seguridad de la cadena de suministro (Supply Chain)

- Dependencias fijadas (lockfiles), auditadas (**Trivy/Grype**) y actualizadas
  (Renovate) con revisión.
- **SAST** (Semgrep/CodeQL) y **secret scanning** en cada PR.
- Imágenes base mínimas (distroless), escaneadas; **SBOM** por artefacto.
- **Firma de artefactos** (Sigstore/cosign) y verificación en el clúster
  (política de admisión: solo imágenes firmadas).
- Políticas del clúster con **OPA/Kyverno** (no-root, no *privileged*, límites).

## 7. Seguridad específica de trading

- **Idempotencia** de órdenes: una orden nunca se ejecuta dos veces (claves de
  idempotencia end-to-end).
- **Pre-trade risk** bloqueante e inevitable: ninguna orden live sin
  `RiskApproved`.
- **Kill-switch** global y por tenant/estrategia (parada de emergencia).
- **Límites y rate-limits** por tenant y por estrategia (protección frente a
  bucles/errores que disparen miles de órdenes).
- **Segregación de modos** paper/live imposible de confundir (tipos distintos,
  confirmaciones explícitas).
- **Reconciliación** continua con el broker: detectar y alertar divergencias de
  posiciones/órdenes.

## 8. Seguridad de aplicación (AppSec)

- Validación estricta de entrada en la frontera (Pydantic); *output encoding*
  para evitar XSS; consultas parametrizadas (no SQL dinámico) → anti-inyección.
- Cabeceras de seguridad (CSP, HSTS, etc.), CSRF donde aplique.
- **Rate limiting** y protección **DDoS**/WAF en el edge (Cloudflare/API GW).
- Manejo de errores que **no filtra** stack traces ni detalles internos.
- Protección de deserialización y de SSRF en los ACLs a terceros.

## 9. Auditoría, detección y respuesta

- **Audit ledger** append-only e inmutable de acciones sensibles (órdenes,
  cambios de riesgo, accesos, credenciales) — base para forense y cumplimiento.
- **SIEM**: centralización de logs de seguridad, detección de anomalías,
  alertas.
- Runbooks de respuesta a incidentes; *post-mortems* sin culpa.
- Pentest externo periódico y programa de *bug bounty* (fase de escala).

## 10. Cumplimiento (roadmap)

- **GDPR/CCPA** desde el diseño (privacidad, consentimiento, borrado).
- **PCI DSS**: fuera de alcance directo delegando pagos en **Stripe** (no
  almacenamos PAN).
- **SOC 2 Type II** e **ISO 27001** como objetivos de Fase 4.
- Requisitos financieros según jurisdicción (p.ej. **MiFID II**, *best
  execution*, retención de registros) evaluados por mercado objetivo.

## 11. Principios rectores

1. **Zero trust**: nada se confía por estar "dentro"; todo se autentica y autoriza.
2. **Least privilege** en usuarios, servicios e infraestructura.
3. **Defense in depth**: múltiples capas; que fallar una no comprometa el sistema.
4. **Secure defaults**: lo seguro es lo fácil; lo inseguro requiere esfuerzo.
5. **Auditabilidad total** de todo lo que toca dinero o datos personales.

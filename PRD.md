# PRD — Marketer Agent (v2)

> **Status:** MVP, posts-only. Conectado vía ROUTER a CONTENT_FACTORY.
> **Última revisión:** 2026-04-21. Reemplaza la versión previa (v1, shape `executor_payload`).
> **Documentos ligados:**
> - `SPEC.md` — contrato técnico + operacional (fuente de verdad para integración)
> - `docs/PERSISTENCE.md` — DB layer: actions configurables + historial de runs + memory por cliente
> - `docs/ROUTER CONTRACT.md` — contrato del orquestador (Contratos A-E)
> - `docs/BRIEF RESPONSE API.md` — forma del gate `brief`

---

## 1. Qué es este servicio

**Marketer** es un microservicio que recibe una tarea ya enrutada por **ROUTER**, aplica razonamiento de marketing con el brief del cliente y el gallery, y devuelve un **enrichment v2 estructurado** listo para que **CONTENT_FACTORY** lo ejecute en contenido publicable.

No enruta, no despacha, no llama a ejecutores, no persiste. ROUTER es su único llamador y único destino de respuesta. El contrato es async: ACK 202 inmediato + callback PATCH con el resultado.

Conceptualmente: **copywriter + estratega senior + director de arte en un solo paso**. Entrega una propuesta de post completa — caption publicable, concepto de imagen, generation prompt, selección de gallery, CTA con canal — más una capa de razonamiento interno que alimenta a futuros subagentes.

---

## 2. El problema que resuelve

Sin marketer, CONTENT_FACTORY recibe sólo el `client_request.description` (frase libre del usuario) + brief crudo (50+ campos mixtos en Spanish/English) + gallery con metadatos. Produce contenido genérico porque:

- El brief está denso pero desestructurado. El craftsman tiene que extraer señales antes de ejecutar.
- No hay ángulo editorial. Cualquier post sobre Casa Maruja podría ser sobre platos, equipo, historia — sin dirección.
- No hay consistencia tonal entre posts del mismo cliente. Cada llamada reinterpreta la voz.
- No hay decisión estratégica visible (por qué este ángulo vs otros). El output es un black box.

Marketer resuelve esto entregándole a CF un brief **editorial completo y publicable**, con:
- Decisiones estratégicas explícitas (angle, voice, surface_format) con alternativas descartadas y rationale.
- Caption completa en 3 bloques (hook, body, cta_line) redactada en el idioma del brief.
- Image concept + generation_prompt concreto + alt_text.
- Selección de gallery con uso/referencia/descarte.
- CTA estructurado (channel, url_or_handle, label) coherente con el caption.
- Hashtags listos (`tags[]` con `#` prefix) además de dirección de intent.
- Un bloque narrativo de marca (`brand_dna`) que CF usa como referencia de design system.
- Una capa interna (`brand_intelligence`) con taxonomía, funnel, persona, ventaja única — para futuros subagentes que refinan este enrichment.
- Un `cf_post_brief` compacto: la instrucción directa que el diseñador/copywriter de CF lee antes de ejecutar.

---

## 3. Scope MVP

### 3.1 Acciones soportadas

MVP: `action_code ∈ {create_post, edit_post}` **habilitadas**. `create_web` / `edit_web` registradas pero **disabled**.

**Driven por DB** (ver `docs/PERSISTENCE.md §2.1`): la tabla `marketer_actions` controla qué acciones están habilitadas, su surface/mode, y a qué prompt overlay mapean. Activar/desactivar una acción es un UPDATE de una fila; no requiere deploy. Añadir una nueva acción es INSERT + crear el overlay file + confirmar que respeta el contrato `PostEnrichment`.

Fallback: si la DB no está accesible, marketer cae a un conjunto hardcoded en código (`create_post`, `edit_post` enabled; web disabled). Degraded mode, alerta a monitoreo.

### 3.2 Surface formats

`post`, `story`, `reel`, `carousel`. El normalizer detecta el surface del `user_request` con patrones estrictos ("story", "reel", "carrusel"). Si no detecta, el LLM elige libremente; el prompt sesga a `post` cuando `post_content_style == "image_text"` y no hay señal explícita.

### 3.3 Downstream

Marketer entrega al router. Router pasa `output_data.enrichment` a CONTENT_FACTORY, que consume especialmente `caption`, `image.generation_prompt`, `visual_selection`, `cta`, `hashtag_strategy.tags`, `brand_dna` y `cf_post_brief`.

Los campos `brand_intelligence`, `strategic_decisions.rationale/alternatives`, `title`, `objective`, `confidence` son razonamiento interno. CF puede ignorarlos; futuros subagentes (rrss_specialist, web_specialist) los heredan para no reinferir.

---

## 4. Input

ROUTER dispatcha al endpoint `POST /tasks` con el envelope completo (`task_id`, `action_code`, `callback_url`, `payload.{client_request, context, action_execution_gates, agent_sequence}`). Ver `SPEC.md §3` para la forma exacta y `docs/ROUTER CONTRACT.md §3` para el contrato autoritativo.

**Fields críticos que marketer consume:**
- `payload.client_request.description` — la petición del usuario.
- `payload.context.account_uuid`, `client_name`, `platform`, `post_id` (edit).
- `payload.action_execution_gates.brief` — brief del cliente (ver `docs/BRIEF RESPONSE API.md`).
- `payload.action_execution_gates.image_catalog` — gallery disponible.

**Parsing policy:** lenient (`extra="allow"` en Pydantic). Campos desconocidos se ignoran sin romper. Campos faltantes producen warnings, no fallos (excepto los 4 requeridos: `task_id`, `action_code`, `callback_url`, `payload.client_request.description`).

---

## 5. Output (v2 shape)

Callback PATCH al `callback_url` del envelope con:

```json
{
  "status": "COMPLETED | FAILED",
  "output_data": {
    "enrichment": { ... schema v2.0 ... },
    "warnings": [ ... ],
    "trace": { ... }
  },
  "error_message": null
}
```

### 5.1 `enrichment` — campos (schema_version: "2.0")

Bloques **public** (consumidos por CF) y **internal** (para subagentes futuros).

| Campo | Visibilidad | Propósito |
|---|---|---|
| `schema_version` | — | "2.0" literal |
| `surface_format` | public | post/story/reel/carousel |
| `content_pillar` | public | product/education/community/etc. |
| `title` | internal | título corto interno (consola, no se publica) |
| `objective` | internal | outcome de negocio en una línea |
| `brand_dna` | **public** | **Design-system reference document**. CLIENT DNA header, colors (hex+rol+nombre), design style, typography, logo rules, contact. 200-400 palabras. Viaja a CF como `client_dna`. |
| `strategic_decisions.{surface_format,angle,voice}` | public+internal | `chosen` se publica; `alternatives_considered` y `rationale` son razonamiento |
| `visual_style_notes` | public | cues de paleta/luz/encuadre con hexes literales del brand |
| `narrative_connection` | public | null standalone; nombre de serie si aplica |
| `image.{concept,generation_prompt,alt_text}` | public | concept humano, prompt al generador, alt text accesibilidad |
| `caption.{hook,body,cta_line}` | **public** | **caption publicable ya**. hook 1-2 líneas, body 1-3 párrafos, cta_line no-accionable si channel=none |
| `cta.{channel,url_or_handle,label}` | **public** | CTA estructurado (channel ∈ available_channels; url_or_handle solo para web/phone/email; label es el copy del botón) |
| `hashtag_strategy.{intent,suggested_volume,themes,tags}` | public | `tags[]` con `#` prefix, 5-10 items, listos para pegar |
| `do_not` | public | lista de anti-patrones (max 5) para CF |
| `visual_selection.{recommended_asset_urls,recommended_reference_urls,avoid_asset_urls}` | public | selección concreta de gallery — qué usar, qué estudiar, qué evitar |
| `confidence.{surface_format,angle,palette_match,cta_channel}` | internal | high/medium/low por decisión |
| `brand_intelligence.*` | **internal** | taxonomía, funnel stage, voice register, emotional beat, audience persona, unfair advantage, risk flags, rhetorical device (8 campos; ver SPEC §5) |
| `cf_post_brief` | **public** | **instrucción compacta ready-to-execute para CF**. Bloque narrativo con editorial image note + Caption assembled + Hashtags. Este es lo que diseñador+copywriter leen primero. |

### 5.2 `warnings` — taxonomía

Warnings no-bloqueantes que marketer emite cuando el contexto es débil o el LLM hizo algo sospechoso. La lista completa vive en `SPEC.md §8`; los más relevantes:

- `brief_missing`, `brief_field_missing`, `value_proposition_empty`, `tone_unclear`
- `gallery_empty`, `gallery_all_filtered`, `gallery_partially_filtered`, `gallery_truncated`
- `context_missing_id` (edit sin `post_id`)
- `request_vague`, `brief_request_mismatch`
- `palette_mismatch`, `claim_not_in_brief`, `visual_hallucinated`, `reference_used_as_asset`
- `cta_channel_invalid`, `cta_url_invalid`, `cta_caption_channel_mismatch`
- `schema_repair_used` (parse falló una vez, repair exitoso)

### 5.3 `trace` — observabilidad por run

Campos: `task_id`, `action_code`, `surface`, `mode`, `latency_ms`, `gemini_model`, `repair_attempted`, `degraded`, `gallery_stats{raw_count, accepted_count, rejected_count, truncated}`.

`degraded=true` cuando `brief_missing`, `gallery_empty` o `gallery_all_filtered` están presentes.

---

## 6. Comportamiento de degradación

Ninguna condición de brief/gallery débil produce `FAILED`. Hay dos reglas:

1. **Best-effort con warnings.** Brief faltante, gallery vacía, tone unclear → output completo con `degraded=true` y warnings explícitos. El router y el usuario deciden qué hacer con un `degraded=true`.
2. **Honestidad en campos sintetizados.** Cuando el brief es demasiado débil para derivar un `brand_intelligence.unfair_advantage` creíble, el prompt instruye al LLM a escribir `"dato insuficiente en el brief"` en vez de inventar. `confidence.*` baja a `low` donde corresponde.

`FAILED` se reserva para:
- `action_code` fuera de `{create_post, edit_post, create_web, edit_web}` → `unsupported_action_code`
- `create_web` / `edit_web` → `web_not_supported_in_this_iteration`
- `edit_post` sin `prior_post` en el envelope → `prior_post_missing`
- Schema Pydantic falla tras 1 repair → `schema_validation_failed`
- Excepción no manejada → `internal_error`

---

## 7. Non-goals (explícito, para no drift)

- No llama a CONTENT_FACTORY ni a ATLAS. Nunca.
- No enruta ni decide el próximo step. ROUTER decide.
- No lee imágenes pixel-level (no multimodal aún). Usa tags/descripciones textuales del gallery.
- No hace búsqueda web. Solo llamada a Gemini.
- No genera imágenes. Produce `image.generation_prompt` para el generador downstream.
- No se auto-crítica con un segundo LLM pass (no self-critique semántico en MVP).
- No distingue entre actions que no sean los 4 listados; cualquier otro action_code es `FAILED`.

---

## 8. Integración

### 8.1 ROUTER

- Se registra como agente en `agents` con `endpoint_url = https://<marketer-host>`, `auth_token = <INBOUND_TOKEN>`, `timeout_seconds = 60-90`.
- Se mapea a `action_catalog` con `action_code ∈ {create_post, edit_post}` apuntando a su `agent_id`.
- Entra en el `agent_sequence` de cada action, típicamente como step 1 (antes de CONTENT_FACTORY).
- Responde 202 ACK en <500ms. El trabajo real corre en background; resultado via PATCH al `callback_url` del envelope.

### 8.2 CONTENT_FACTORY

Consume `output_data.enrichment` de la respuesta de marketer. Campos que CF usa directamente:

- `cf_post_brief` — instrucción compacta (la lee primero)
- `caption.hook/body/cta_line` — copy para renderizar
- `image.generation_prompt` / `visual_selection.recommended_asset_urls` — source visual
- `cta.channel` + `cta.label` / `cta.url_or_handle` — link sticker/botón
- `hashtag_strategy.tags` — hashtags literales
- `brand_dna` — referencia de design system (paleta, tipografía, tono de marca)
- `do_not` — guardarraíles
- `surface_format` — formato final

Campos que CF puede ignorar: `title`, `objective`, `strategic_decisions.{rationale,alternatives_considered}`, `confidence.*`, `brand_intelligence.*` (son para subagentes futuros o consola).

---

## 8.3 Persistencia y memory por cliente

Marketer persiste y recuerda. Ver `docs/PERSISTENCE.md` para schema + flujo detallados.

**Qué se guarda:**
- **`marketer_runs`**: un row por cada task recibida (COMPLETED o FAILED). Conserva el envelope completo y el enrichment producido. Append-only. Retención default 90 días. Sirve para audit, replay, y alimentar el siguiente bloque.
- **`marketer_client_memory`**: un row por `account_uuid`. Agrega el "estado actual" del cliente desde la perspectiva de marketer: últimos ángulos usados, distribución de content_pillars, distribución de CTA channels, `brand_dna_cached` + `brand_intelligence_static` reutilizables. Se refresca tras cada run COMPLETED.

**Qué hace con la memory:**
Al llegar un request, marketer carga la memory por `account_uuid` y la inyecta en el prompt. El LLM la usa para:
- Evitar repetir ángulos recientes (consistency + diversidad).
- Re-balancear content_pillars si hay over-reliance en uno.
- Reutilizar `brand_dna` cacheado si el brief no cambió (ahorra tokens + mantiene narrativa consistente).
- Heredar las partes estables de `brand_intelligence` (taxonomía, voice_register, unfair_advantage, audience_persona) sin re-inferir.

**Qué NO se guarda:**
- Secretos, credenciales, tokens.
- Información derivable del envelope sin valor estratégico (headers, timestamps redundantes).

---

## 9. MVP acceptance

El MVP se acepta cuando:

- ✅ `POST /tasks` acepta el envelope de ROUTER y responde 202 en <500ms
- ✅ Fondo: normaliza, llama Gemini, valida, PATCHea callback
- ✅ `status=COMPLETED` con `output_data.enrichment` conforme al schema v2.0 en posts válidos
- ✅ `FAILED` solo en los 5 casos del §6 (nunca por brief débil / gallery vacía)
- ✅ `warnings[]` refleja degradación de contexto
- ✅ `trace.degraded=true` cuando `brief_missing` o `gallery_empty`
- ✅ `visual_selection.recommended_asset_urls` es subconjunto del gallery sanitizado (validator enforza)
- ✅ `cta.channel` siempre ∈ `available_channels` (o `none` si ninguno aplica)
- ✅ Auth inbound `Authorization: Bearer <INBOUND_TOKEN>` + callback outbound `X-API-Key: <ORCH_CALLBACK_API_KEY>` funcionan
- ✅ Retry de callback: 2 intentos con backoff exponencial en 5xx/network
- ✅ Suite de tests pasa: 36 offline + 26 live golden (62 total)

Estado al 2026-04-21: **todos cumplidos**.

---

## 10. Success metrics (post-launch, no-MVP)

Una vez conectado a ROUTER + CF reales, las métricas que cuentan:

- `marketer_tasks_received_total{action_code}`
- `marketer_tasks_completed_total{action_code, degraded}` vs `_failed_total{action_code, reason}`
- `marketer_end_to_end_latency_seconds` histogram (ACK → PATCH callback): p50 objetivo ~12s, p95 ~18s, p99 <30s
- `marketer_warnings_emitted_total{code}` — distribución; `palette_mismatch` o `claim_not_in_brief` sostenidos señalan drift del prompt
- `marketer_schema_repairs_total` — >5% de runs con repair señala prompt bug
- CF downstream acceptance: tasa de posts aceptados por el usuario final después de generarse. Esto es la métrica REAL — sin feedback de CF, el resto es vanity.

---

## 11. Open items (que bloquearán cuando se despliegue)

Estos items no bloquean el spec ni el build. Se tornan bloqueantes al integrar a ROUTER real:

1. **Registro del agente en ROUTER**: alguien con acceso a la BD del router ejecuta el `INSERT agents / action_catalog / agent_sequence`.
2. **Image catalog gate**: confirmar con ROUTER el `gate_code` canónico (hoy el normalizer detecta cualquier gate cuyo `response.data` sea lista de imágenes). Ideal: fijar un nombre estable.
3. **Role per image**: si ROUTER va a pasar `role ∈ {brand_asset, content, reference, unknown}` explícito por imagen, confirmar el campo. Hoy el normalizer infiere por source location.
4. **Timeout del step**: recomendar 90s (p95 real ~18s deja margen), `agent_sequence.current.timeout_seconds=90`.
5. **output_schema registration**: marketer publica `post_enrichment.v2`; ROUTER puede activar validación schema-based en callbacks cuando quiera.
6. **Despliegue**: quién corre el container (infra team) y dónde (ECS/Cloud Run/K8s).
7. **DB provisioning**: marketer requiere PostgreSQL dedicada (recomendado) o compartida. Schema en `docs/PERSISTENCE.md §2`; migrations con Alembic. Decisión pendiente con infra team.

---

## 12. Fuera de MVP (futuras iteraciones)

Documentado para no confundir con scope actual:

- **Web (`create_web`, `edit_web`)**: overlays existen en `src/marketer/llm/prompts/`. Desbloquear cuando ATLAS esté integrado.
- **Multimodal vision**: pasar imágenes del gallery como `Part` a Gemini para juicio visual real, no por tags.
- **Self-critique pass**: segundo LLM call que audita el output; convierte problemas semánticos a warnings.
- **Caching de brand_profile**: brand_dna + partes estables de brand_intelligence son client-level. Cachear por `(account_uuid, brief_uuid)` ahorra 30-40% de latencia/costo por post.
- **Subagentes internos**: `rrss_creative_specialist`, `web_specialist` que consumen este enrichment y producen variaciones.
- **Subagentes externos (router-orchestrated)**: más ambicioso, vía `agent_sequence` multi-step con `depends_on_*` del ADR.

Ver `docs/ADR PAYLOAD.md` para el marco de evolución (embed + projection + output_schema validation).

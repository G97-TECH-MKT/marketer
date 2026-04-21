# OpenSpec - ADR: payload incremental del router, limites y evolucion

Estado: **propuesta (no implementada)**. Este documento es un ADR vivo. Explica por que el patron actual de "pasar todo el historial en el body al agente" funciona hoy pero no escala, y propone un plan concreto de evolucion, con ejemplos de produccion, cambios en base de datos y nuevos campos.

**Revision 2 - 2026-04-20 - embed primero (decision de producto):**
El agente final debe recibir **incrustada (por valor)** la informacion del/los pasos predecesores que necesita. **No** se quiere que el agente llame a un endpoint externo del orquestador para "ir a buscar" el output del paso X. La configuracion vive en BD: `depends_on_steps` define **que pasos previos** se incrustan; opcionalmente `previous_projection` define **que campos** de cada predecesor se incrustan. La variante "reference" (pass-by-reference via GET) queda como **valvula de seguridad para overflow**, no como ruta principal.

Archivos relacionados:
- `flujo-router-prerequisitos-gates-secuencia.md` - flujo actual.
- `contrato-payload-dispatch-agente.md` - contrato actual.
- `ejemplo-json-body-agente-y-gates.md` - ejemplo canonico actual.
- `modelo-de-datos-erd.md` - modelo actual.

## 1. Contexto

Cuando un `Job` corre una `Action` con `agent_sequence` de N pasos:

- `_validate_execution_gates` guarda **una entrada por gate** en `jobs.metadata.gate_responses` (dict por `gate_code`).
- `_record_sequence_outcome` guarda **una entrada por paso completado** en `jobs.metadata.sequence_responses` (dict por `step_code`).
- `_build_dispatch_payload` serializa **todos los gates** + **todos los pasos previos** en el body HTTP que recibe cada agente siguiente.

Es decir, al paso N el orquestador le manda:

```
payload_size(N)  =  sizeof(gates)  +  Σ sizeof(output_data[i])   para i ∈ {1..N-1}
```

En el paso 1 eso es pequeno. En el paso 10 o 20 con outputs ricos es multi-MB.

## 2. Problemas del patron actual

Resumen corto. El analisis extendido vive en el cuerpo de este ADR.

1. **Crecimiento lineal del payload HTTP.** Timeouts, RAM, coste de red, limites de proxies.
2. **Retries amplifican.** Cada reintento reenvia todo el historial.
3. **Menor privilegio violado.** Cada agente ve datos de todos los pasos previos aunque no los necesite.
4. **Acoplamiento implicito.** Cualquier agente puede leer la forma interna de otro sin contrato declarado ni versionado.
5. **`jobs.metadata` JSONB se reescribe completo en cada callback.** Coste en TOAST y race conditions si algun dia hay pasos paralelos.
6. **No hay schema de `output_data` por paso.** Bugs tipograficos se detectan en produccion.
7. **Observabilidad limitada.** No hay forma nativa de saber **que vio exactamente** el agente en el instante del dispatch; se reconstruye por replay del dict.
8. **Seguridad/PII.** El brief va entero a agentes que solo necesitan un subconjunto.

## 3. Decision arquitectonica propuesta

Mover a un modelo de **cuatro cambios combinados**, con **embed por valor** como ruta principal:

### 3.1 Dependencias declarativas por paso (cambio barato, alto impacto)

Cada paso en `agent_sequence` declara **que gates y que pasos previos necesita**. El orquestador inyecta **solo** lo declarado, y lo hace **por valor** (embed) directamente en el body HTTP.

**Nuevas columnas en `agent_sequence`:**

| Columna | Tipo | Proposito |
|---------|------|-----------|
| `depends_on_gates` | `text[] NOT NULL DEFAULT '{}'` | Lista de `gate_code` del catalogo a incluir en `action_execution_gates`. |
| `depends_on_steps` | `text[] NOT NULL DEFAULT '{}'` | Lista de `step_code` previos cuyo `output_data` se embebe en `agent_sequence.previous`. Orden respetado por `sequence_order`. |
| `previous_projection` | `jsonb NULL` | Opcional. Por `step_code`, lista de rutas JSONPath o claves a conservar del `output_data` del predecesor. Si es `NULL` se embebe el `output_data` entero. |
| `gates_projection` | `jsonb NULL` | Opcional. Por `gate_code`, rutas a conservar de `response`. Si es `NULL` se embebe el `response` entero. |
| `inject_policy` | `varchar(20) NOT NULL DEFAULT 'embed'` | `embed` (default, recomendado) o `reference` (valvula de seguridad; solo se aplica si `max_input_bytes` es superado o el producto lo pide explicito). |
| `max_input_bytes` | `integer NULL` | Tope duro para el `payload.agent_sequence.previous`. Si se excede con `embed`, el orquestador **falla con error explicito** `PayloadTooLargeError` (o degrada a `reference` si `inject_policy=reference`). No hay truncado silencioso. |
| `output_schema` | `varchar(100) NULL` | Nombre de esquema que el agente de este paso produce. Sirve para validar callback. |

Fallback compatible: si `depends_on_steps` y `depends_on_gates` estan vacios -> **incrusta todo** como hoy (comportamiento legacy) para que la migracion sea gradual.

### 3.2 Snapshot persistente de outputs por paso (observabilidad y auditoria)

Nueva tabla `job_step_outputs` donde el orquestador guarda el `output_data` **completo** de cada paso. **No es un endpoint que los agentes consulten.** Sirve para:

- observabilidad (UI de la consola muestra tamanos, schema, diff),
- replay/debug del job sin bucear en `metadata` jsonb,
- desacoplar retencion (TTL distinto del job),
- evitar rewrite del JSON gigante de `jobs.metadata` en cada callback.

```sql
CREATE TABLE job_step_outputs (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id            uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  task_id           uuid NOT NULL REFERENCES job_tasks(id) ON DELETE CASCADE,
  step_code         varchar(100) NOT NULL,
  sequence_order    integer NOT NULL,
  agent_id          uuid NOT NULL REFERENCES agents(id),
  output_schema     varchar(100),
  output_data       jsonb NOT NULL,
  size_bytes        integer NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_job_step_outputs UNIQUE (job_id, step_code)
);
CREATE INDEX idx_job_step_outputs_job ON job_step_outputs(job_id);
```

El orquestador sigue usando este snapshot **internamente** para construir el `embed` del proximo dispatch (en lugar de leer del JSON del job).

### 3.3 Embed filtrado y proyectado (ruta principal)

Cada dispatch construye el payload asi:

1. Toma el step actual y lee `depends_on_gates`, `depends_on_steps`, `gates_projection`, `previous_projection`.
2. Para `action_execution_gates`: incluye **solo** los `gate_code` en `depends_on_gates`. Si hay `gates_projection` para ese code, aplica las rutas.
3. Para `agent_sequence.previous`: por cada `step_code` en `depends_on_steps`, lee `job_step_outputs.output_data` (o `sequence_responses.output_data` legacy), aplica `previous_projection` si existe, y **lo incrusta completo** en `previous[step_code].output_data`.
4. Calcula el tamano del sub-objeto `previous` + `action_execution_gates`. Si supera `max_input_bytes` del step:
   - con `inject_policy=embed`: levanta `PayloadTooLargeError` y marca el task FAILED con traza clara (es un error de diseno, no de runtime).
   - con `inject_policy=reference`: degrada a reference (ver 3.5) y loggea warning.

El agente final **recibe todo incrustado** en un solo POST HTTP. No hay GETs adicionales.

### 3.4 Contrato de outputs tipado

Nuevo campo `output_schema` en `agent_sequence` (por paso). El callback valida con JSONSchema/pydantic en el orquestador cuando el agente responde `COMPLETED`. Habilita versionado sin breaking changes: `marketer.v1` -> `marketer.v2`.

Si un paso consumidor declara `depends_on_steps=[prev]` y el `prev.output_schema` cambia de version, la BD deja trazable que el consumidor necesita actualizarse.

### 3.5 Reference como valvula de seguridad (opcional)

Solo para casos excepcionales (outputs >5 MB que no se pueden proyectar y el agente no puede recibirlos por valor). No es la ruta de diseno. Detalle en apendice `## A. Variante reference para overflow`.

### 3.6 Contrato de respuesta del agente (cerrar el ciclo)

Cada agente, tras ejecutar su tarea, llama a `PATCH /api/v1/tasks/{task_id}/callback` con:

- `status`: uno de `IN_PROGRESS` (opcional, progreso), `COMPLETED`, `FAILED`, `TIMEOUT`.
- `output_data`: objeto libre si `COMPLETED` (validado contra `output_schema` si esta definido).
- `error_message`: string si `FAILED` o `TIMEOUT`.

El orquestador:

1. Aplica transiciones `TASK_STATUS_TRANSITIONS`. Invalida -> 409.
2. Si `COMPLETED` y hay `output_schema`, valida. Invalida -> 422.
3. Upsert en `job_step_outputs` (solo en terminal `COMPLETED`).
4. Actualiza `sequence_responses[step_code]` con **solo resumen** (sin `output_data`).
5. Reevalua `JobStatus` global y, si terminal, notifica a `initiator_callback_url` (ver 3.7).

Forma exacta del body en formato JSONSchema resumida:

```json
{
  "status": "COMPLETED",
  "output_data": { "...": "schema definido por output_schema del paso" },
  "error_message": null
}
```

### 3.7 Notificacion terminal al iniciador (opcional)

Si el `CreateJobRequest` incluyo `initiator_callback_url`, al llegar el job a estado terminal el orquestador hace POST a esa URL con este contrato fijo:

```json
{
  "event": "job_terminal",
  "job_id": "...",
  "action_code": "...",
  "status": "COMPLETED | FAILED | TIMEOUT",
  "correlation_id": "...",
  "output_data": {},
  "error_message": null,
  "gate_responses": {},
  "sequence_responses": {},
  "tasks": [
    {
      "task_id": "...",
      "status": "...",
      "agent_id": "...",
      "sequence_order": 1,
      "step_code": "...",
      "is_mandatory": true,
      "retry_count": 0,
      "error_details": null
    }
  ]
}
```

`output_data` aqui es el output del **ultimo task** con output (o el unico si no hay secuencia). Para obtener el output de un paso intermedio concreto se usa `GET /api/v1/jobs/{job_id}` o la tabla `job_step_outputs`.

## 4. Escenarios reales y JSON de ejemplo

Todos los escenarios asumen el **modelo propuesto** con **embed por valor** y configuracion declarativa (`depends_on_gates`, `depends_on_steps`, proyecciones opcionales). Ningun agente hace GET al orquestador para obtener datos.

---

### Escenario A - Crear post de Instagram con Content Factory (3 pasos, **embed**)

Ejemplo canonico. Alineado al caso que envio producto: el agente final (`content_factory`) recibe del paso 2 (`copy_generator`) un `output_data` con `total_items`, `client_dna`, `client_request` y `resources`. Esos campos se incrustan **literalmente** dentro de `agent_sequence.previous.copy_generator.output_data`. No hay `output_ref`. No hay GET.

**Action:** `create_ig_post`.

**Gates:**
1. `brief` (GET brief del cliente).
2. `image_catalog` (GET imagenes activas de la cuenta).
3. `account_platform` (GET cuenta IG conectada).

**Secuencia configurada en BD (`agent_sequence`):**

| orden | step_code | agent_name | depends_on_gates | depends_on_steps | previous_projection | inject_policy |
|------:|-----------|------------|------------------|------------------|---------------------|---------------|
| 1 | `post_planner` | `marketer_planner` | `brief`, `image_catalog` | `{}` | `NULL` | embed |
| 2 | `copy_generator` | `content_factory_copy` | `brief` | `{post_planner}` | `NULL` (usa todo `post_planner`) | embed |
| 3 | `content_factory` | `content_factory` | `{}` (el copy ya condenso lo que hacia falta) | `{copy_generator}` | `NULL` (usa todo `copy_generator`) | embed |

El paso 3 no recibe `action_execution_gates.brief` ni `image_catalog` porque `depends_on_gates=[]`. No recibe `post_planner` porque `depends_on_steps=[copy_generator]`. Recibe **solo** el `output_data` completo del `copy_generator`.

**`CreateJobRequest` inicial (lo que envia el conversacional):**

```json
{
  "action": "create_ig_post",
  "context": {
    "account_uuid": "8a095bf8-f9b7-47a5-9d4a-5933983ba95f",
    "client_name": "Casa Maruja",
    "platform": "instagram",
    "post_id": "post_1299001"
  },
  "client_request": {
    "description": "Crea un post destacando el plato estrella de esta semana, con descripción del producto de temporada y el por qué lo hemos elegido. Tono cercano, apetecible y sin florituras",
    "attachments": []
  },
  "correlation_id": "ig-20260420-007"
}
```

**Callback del paso 2 (`copy_generator`) al orquestador** (asi queda el output que alimenta al paso 3):

```json
{
  "status": "COMPLETED",
  "output_data": {
    "total_items": 2,
    "client_dna": "Bienvenidos a Casa Maruja\nCocina de mercado hecha con cariño, en el corazón del barrio.\n\nNuestra Historia\nCasa Maruja nació del recetario de una abuela y la terquedad de una nieta que no quería que esas recetas desaparecieran. Abrimos en 2019 en el barrio de Ruzafa, Valencia, con la firme convicción de que comer bien no tiene que ser caro ni complicado — solo tiene que saber a algo.\n\nNuestra propuesta\nCocina de mercado de temporada. Cada semana visitamos el Mercado Central y construimos el menú en función de lo que el campo trae. No hay carta fija; hay producto fresco y recetas de siempre.\n\nEspecialidades\n- Arròs al forn de los lunes\n- Croquetas de puchero (receta de la abuela Maruja, intransferible)\n- Menú del día: 12 € — primero, segundo, postre casero y bebida\n\nEstilo visual\nTonos tierra, mostaza y verde oliva. Fotografía de comida natural, sin filtros exagerados. Tipografía con carácter artesanal. Transmitimos calidez, honestidad y producto.\n\nCalle dels Literatos, 8, Ruzafa, Valencia\n963 00 11 22\nwww.casamaruja.es\nhola@casamaruja.es",
    "client_request": "Crea un post destacando el plato estrella de esta semana, con descripción del producto de temporada y el por qué lo hemos elegido. Tono cercano, apetecible y sin florituras.",
    "resources": [
      "https://i.pinimg.com/736x/00/2f/ef/002fefd0c200e93fd65f823cac70ed05.jpg"
    ]
  }
}
```

**Dispatch exacto al paso 3 (`content_factory`)** — lo que realmente viaja por HTTP:

```json
{
  "task_id": "eeee5555-eeee-5555-eeee-555555555555",
  "job_id": "ffff6666-ffff-6666-ffff-666666666666",
  "action_code": "create_ig_post",
  "action_id": "7777aaaa-7777-aaaa-7777-aaaa77777777",
  "correlation_id": "ig-20260420-007",
  "payload": {
    "client_request": {
      "description": "Crea un post destacando el plato estrella de esta semana, con descripción del producto de temporada y el por qué lo hemos elegido. Tono cercano, apetecible y sin florituras",
      "attachments": []
    },
    "context": {
      "account_uuid": "8a095bf8-f9b7-47a5-9d4a-5933983ba95f",
      "client_name": "Casa Maruja",
      "platform": "instagram",
      "post_id": "post_1299001"
    },
    "action_execution_gates": {},
    "agent_sequence": {
      "current": {
        "step_code": "content_factory",
        "step_order": 3,
        "task_id": "eeee5555-eeee-5555-eeee-555555555555",
        "agent_id": "8888bbbb-8888-bbbb-8888-bbbb88888888",
        "agent_name": "content_factory",
        "endpoint": "https://webhook-dev.plinng.com/tasks",
        "http_method": "POST",
        "is_mandatory": true,
        "timeout_seconds": 120,
        "retry_count": 0
      },
      "previous": {
        "copy_generator": {
          "status": "COMPLETED",
          "sequence_order": 2,
          "output_schema": "ig_copy.v1",
          "output_data": {
            "total_items": 2,
            "client_dna": "Bienvenidos a Casa Maruja\nCocina de mercado hecha con cariño, en el corazón del barrio.\n\nNuestra Historia\nCasa Maruja nació del recetario de una abuela y la terquedad de una nieta que no quería que esas recetas desaparecieran. Abrimos en 2019 en el barrio de Ruzafa, Valencia, con la firme convicción de que comer bien no tiene que ser caro ni complicado — solo tiene que saber a algo.\n\nNuestra propuesta\nCocina de mercado de temporada. Cada semana visitamos el Mercado Central y construimos el menú en función de lo que el campo trae. No hay carta fija; hay producto fresco y recetas de siempre.\n\nEspecialidades\n- Arròs al forn de los lunes\n- Croquetas de puchero (receta de la abuela Maruja, intransferible)\n- Menú del día: 12 € — primero, segundo, postre casero y bebida\n\nEstilo visual\nTonos tierra, mostaza y verde oliva. Fotografía de comida natural, sin filtros exagerados. Tipografía con carácter artesanal. Transmitimos calidez, honestidad y producto.\n\nCalle dels Literatos, 8, Ruzafa, Valencia\n963 00 11 22\nwww.casamaruja.es\nhola@casamaruja.es",
            "resources": [
              "https://i.pinimg.com/736x/00/2f/ef/002fefd0c200e93fd65f823cac70ed05.jpg"
            ]
          },
          "retry_count": 0,
          "started_at": "2026-04-20T11:00:09+00:00",
          "completed_at": "2026-04-20T11:00:20+00:00",
          "duration_ms": 11000
        }
      }
    }
  },
  "callback_url": "https://d1lwu6lioovdrb.cloudfront.net/api/v1/tasks/eeee5555-eeee-5555-eeee-555555555555/callback"
}
```

Observaciones claves:
- `action_execution_gates` esta **vacio** porque el paso 3 no declara `depends_on_gates`. Si un dia quisieras volver a meterle, por ejemplo, `image_catalog`, basta con editar `depends_on_gates=['image_catalog']` en BD para ese step.
- `previous` contiene **solo** `copy_generator`. `post_planner` no viaja porque no esta en `depends_on_steps` del paso 3.
- `previous.copy_generator.output_data` es **identico** al que devolvio el agente en el callback. No hay proyeccion porque `previous_projection` es `NULL` para ese par.
- `client_request` y `context` siempre viajan porque son parte del request original del cliente (esto se mantiene asi siempre, no esta sujeto a dependencias).

**Variante con `previous_projection`**: si por ejemplo el paso 3 solo necesita `client_dna` y `resources` (sin `total_items` ni `client_request` del paso 2), la configuracion seria:

```json
{
  "copy_generator": ["client_dna", "resources"]
}
```

Y el `output_data` embebido quedaria:

```json
{
  "client_dna": "Bienvenidos a Casa Maruja...",
  "resources": ["https://i.pinimg.com/736x/00/2f/ef/002fefd0c200e93fd65f823cac70ed05.jpg"]
}
```

---

### Escenario B - Crear una web (3 pasos, **embed**, predecesor inmediato condensa todo)

**Action:** `create_web`.

**Gates:** `brief`, `domain_availability`, `legal_terms`.

**Secuencia:**

| orden | step_code | agent_name | depends_on_gates | depends_on_steps | previous_projection | inject_policy |
|------:|-----------|------------|------------------|------------------|---------------------|---------------|
| 1 | `research_market` | `market_researcher` | `brief` | `{}` | `NULL` | embed |
| 2 | `web_spec_builder` | `web_prompt_engineer` | `brief`, `legal_terms`, `domain_availability` | `{research_market}` | `NULL` | embed |
| 3 | `web_builder_final` | `web_agent` | `{}` | `{web_spec_builder}` | `NULL` | embed |

El paso 2 es el que condensa **toda** la informacion (brief + investigacion) en un unico `output_data` listo para que el paso 3 (el agente que crea webs) lo ejecute sin tener que interpretar datos de mas arriba.

**Callback del paso 2 (`web_spec_builder`)** que deja lista la "orden de fabricacion":

```json
{
  "status": "COMPLETED",
  "output_data": {
    "schema": "web_spec.v1",
    "site_name": "casamaruja.es",
    "domain": "casamaruja.es",
    "language": "es",
    "tone": "calido, cercano, sin florituras",
    "palette": ["#8B5A2B", "#D4A017", "#556B2F"],
    "pages": [
      { "slug": "home",    "title": "Casa Maruja", "sections": ["hero", "menu_del_dia", "especialidades", "historia", "contacto"] },
      { "slug": "carta",   "title": "Nuestra carta", "sections": ["temporada", "especialidades"] },
      { "slug": "contacto","title": "Contacto", "sections": ["formulario", "mapa"] }
    ],
    "copy_blocks": {
      "hero.title": "Cocina de mercado, hecha con cariño",
      "hero.subtitle": "Ruzafa, Valencia",
      "menu_del_dia.description": "12 € — primero, segundo, postre casero y bebida"
    },
    "assets": [
      "https://cdn.example/casamaruja/hero.jpg",
      "https://cdn.example/casamaruja/arros.jpg"
    ],
    "legal": {
      "terms_url": "https://casamaruja.es/terminos",
      "privacy_url": "https://casamaruja.es/privacidad"
    }
  }
}
```

**Dispatch exacto al paso 3 (`web_builder_final`):**

```json
{
  "task_id": "aaaa1111-aaaa-1111-aaaa-111111111111",
  "job_id": "bbbb2222-bbbb-2222-bbbb-222222222222",
  "action_code": "create_web",
  "action_id": "cccc3333-cccc-3333-cccc-333333333333",
  "correlation_id": "web-20260420-001",
  "payload": {
    "client_request": {
      "site_name": "casamaruja.es",
      "goal": "Landing del restaurante + carta + contacto"
    },
    "context": {
      "account_uuid": "9b1c0f12-0d8b-4a46-aea5-2a2cc4b47f21",
      "client_name": "Casa Maruja"
    },
    "action_execution_gates": {},
    "agent_sequence": {
      "current": {
        "step_code": "web_builder_final",
        "step_order": 3,
        "task_id": "aaaa1111-aaaa-1111-aaaa-111111111111",
        "agent_id": "dddd4444-dddd-4444-dddd-444444444444",
        "agent_name": "web_agent",
        "endpoint": "https://webhook-dev.plinng.com/tasks",
        "http_method": "POST",
        "is_mandatory": true,
        "timeout_seconds": 300,
        "retry_count": 0
      },
      "previous": {
        "web_spec_builder": {
          "status": "COMPLETED",
          "sequence_order": 2,
          "output_schema": "web_spec.v1",
          "output_data": {
            "schema": "web_spec.v1",
            "site_name": "casamaruja.es",
            "domain": "casamaruja.es",
            "language": "es",
            "tone": "calido, cercano, sin florituras",
            "palette": ["#8B5A2B", "#D4A017", "#556B2F"],
            "pages": [
              { "slug": "home",    "title": "Casa Maruja", "sections": ["hero", "menu_del_dia", "especialidades", "historia", "contacto"] },
              { "slug": "carta",   "title": "Nuestra carta", "sections": ["temporada", "especialidades"] },
              { "slug": "contacto","title": "Contacto", "sections": ["formulario", "mapa"] }
            ],
            "copy_blocks": {
              "hero.title": "Cocina de mercado, hecha con cariño",
              "hero.subtitle": "Ruzafa, Valencia",
              "menu_del_dia.description": "12 € — primero, segundo, postre casero y bebida"
            },
            "assets": [
              "https://cdn.example/casamaruja/hero.jpg",
              "https://cdn.example/casamaruja/arros.jpg"
            ],
            "legal": {
              "terms_url": "https://casamaruja.es/terminos",
              "privacy_url": "https://casamaruja.es/privacidad"
            }
          },
          "retry_count": 0,
          "started_at": "2026-04-20T10:03:00+00:00",
          "completed_at": "2026-04-20T10:04:22+00:00",
          "duration_ms": 82000
        }
      }
    }
  },
  "callback_url": "https://d1lwu6lioovdrb.cloudfront.net/api/v1/tasks/aaaa1111-aaaa-1111-aaaa-111111111111/callback"
}
```

Notar:
- `web_agent` recibe **todo** lo que necesita incrustado en `previous.web_spec_builder.output_data`.
- `research_market` no aparece porque no esta en `depends_on_steps`.
- `action_execution_gates` viene vacio porque el paso 3 no declara `depends_on_gates` (la especificacion completa ya vino en `web_spec_builder.output_data`).
- Si el dueño del `web_agent` algun dia necesita ver el `brief` original, basta con añadir `brief` a `depends_on_gates` del paso 3 en BD. Zero deploy.

---

### Escenario C - Caso simple (1 solo paso, sin secuencia)

**Action:** `edit_post`. Sin `agent_sequence`. Un unico agente.

No cambia nada respecto al flujo actual. `previous` viene vacio. Se mantiene compatibilidad.

```json
{
  "task_id": "11111111-0000-0000-0000-000000000001",
  "job_id": "22222222-0000-0000-0000-000000000002",
  "action_code": "edit_post",
  "action_id": "33333333-0000-0000-0000-000000000003",
  "correlation_id": "edit-20260420-001",
  "payload": {
    "client_request": { "post_id": "post_1299001", "description": "Refrescar CTA" },
    "context": { "account_uuid": "8a095bf8-f9b7-47a5-9d4a-5933983ba95f" },
    "action_execution_gates": {
      "brief": { "passed": true, "reason": "ok", "status_code": 200, "response": { "brief": { "tone": "friendly" } } }
    },
    "agent_sequence": {
      "current": {
        "step_code": "33333333-0000-0000-0000-000000000003",
        "step_order": 1,
        "task_id": "11111111-0000-0000-0000-000000000001",
        "agent_id": "44444444-0000-0000-0000-000000000004",
        "agent_name": "post_editor",
        "endpoint": "https://webhook-dev.plinng.com/tasks",
        "http_method": "POST",
        "is_mandatory": true,
        "timeout_seconds": 60,
        "retry_count": 0
      },
      "previous": {}
    }
  },
  "callback_url": "https://d1lwu6lioovdrb.cloudfront.net/api/v1/tasks/11111111-0000-0000-0000-000000000001/callback"
}
```

---

## 5. Cambios concretos en base de datos

### 5.1 `agent_sequence` (alter)

```sql
ALTER TABLE agent_sequence
  ADD COLUMN depends_on_gates     text[]        NOT NULL DEFAULT '{}',
  ADD COLUMN depends_on_steps     text[]        NOT NULL DEFAULT '{}',
  ADD COLUMN previous_projection  jsonb         NULL,
  ADD COLUMN gates_projection     jsonb         NULL,
  ADD COLUMN inject_policy        varchar(20)   NOT NULL DEFAULT 'embed'
    CHECK (inject_policy IN ('embed','reference')),
  ADD COLUMN max_input_bytes      integer       NULL,
  ADD COLUMN output_schema        varchar(100)  NULL;

CREATE INDEX idx_agent_sequence_inject_policy ON agent_sequence(inject_policy);
```

Compatibilidad: si `depends_on_gates` y `depends_on_steps` quedan vacios -> comportamiento actual (incrusta todo), no rompe nada.

**Forma de `previous_projection`** (ejemplo):

```json
{
  "copy_generator": ["client_dna", "resources"],
  "post_planner":   ["slots", "theme"]
}
```

Claves = `step_code` predecesor. Valores = lista de **rutas** a conservar del `output_data`. Rutas tipo "dot path" (`data.brief.uuid`). Si no aparece un predecesor, se toma su `output_data` completo.

**Forma de `gates_projection`** (ejemplo):

```json
{
  "brief": ["data.brief.form_values.FIELD_COMMUNICATION_LANGUAGE"],
  "image_catalog": ["items"]
}
```

### 5.2 Nueva `job_step_outputs`

Ver 3.2. Reglas:

- El orquestador inserta/upserta al validar callback `COMPLETED`.
- `ON DELETE CASCADE` desde `jobs` y `job_tasks`.
- Indice por `size_bytes` para alertas de percentil.
- **Uso interno**: lo lee `_build_dispatch_payload` para construir el embed del siguiente paso. **No** se expone como endpoint HTTP consumido por agentes en la ruta principal.

### 5.3 Vista opcional `job_progress_view`

Para consola. Une `jobs`, `job_tasks`, `job_step_outputs` con `agent_name` + `size_bytes` por paso. Evita recorrer `metadata` jsonb desde la UI.

### 5.4 Migracion Alembic

Anadir `alembic/versions/010_payload_incremental_evolucion.py` que:

1. Crea tabla `job_step_outputs`.
2. ALTER de `agent_sequence` con las 7 columnas nuevas.
3. Backfill opcional: no mover historico de `sequence_responses.output_data` a la tabla nueva (compatibilidad hacia atras con jobs antiguos).

## 6. Cambios en codigo

Minimo viable en `src/orchestrator`:

### 6.1 `domain/entities/agent_sequence_step.py`

Nuevos campos:

- `depends_on_gates: list[str]`
- `depends_on_steps: list[str]`
- `previous_projection: dict[str, list[str]] | None`
- `gates_projection: dict[str, list[str]] | None`
- `inject_policy: Literal["embed","reference"]`
- `max_input_bytes: int | None`
- `output_schema: str | None`

### 6.2 `application/services/job_service.py`

`_build_dispatch_payload` cambia a **embed filtrado**:

1. Toma `depends_on_*` y proyecciones del step actual.
2. Construye `action_execution_gates`: solo los `gate_code` de `depends_on_gates`; aplica `gates_projection` si existe.
3. Construye `agent_sequence.previous`: por cada `step_code` en `depends_on_steps`, carga `output_data` desde `job_step_outputs` (fallback: `sequence_responses.output_data`); aplica `previous_projection` si existe; **incrusta el `output_data` literalmente** en `previous[step_code].output_data`.
4. Mide tamaño. Si supera `max_input_bytes` y `inject_policy=embed`, lanza `PayloadTooLargeError` y marca la task FAILED con trace `PAYLOAD_TOO_LARGE` (error de configuracion, no de runtime). Si `inject_policy=reference`, cae al apendice A.

`_record_sequence_outcome` cambia a:

1. Insertar (o upsert) fila en `job_step_outputs` con `output_data` + `size_bytes` + `output_schema`.
2. En `jobs.metadata.sequence_responses[step_code]` guarda **solo** `status`, `task_id`, `agent_id`, `retry_count`, timestamps y `duration_ms`. **No** el `output_data` crudo (para no reescribir el JSON gigante del job en cada callback).

### 6.3 Callback handler

`handle_task_callback` valida `output_data` contra `output_schema` del step cuando hay schema registrado. Si no valida -> `InvalidAgentOutputError` (HTTP 422 al agente).

### 6.4 Frontend (consola)

- UI de `agent_sequence` por step:
  - multi-select de `depends_on_gates` (de los `action_execution_gates` existentes),
  - multi-select de `depends_on_steps` (de los pasos previos de la misma `action`),
  - editor JSON de `previous_projection` y `gates_projection`,
  - picker de `output_schema`.
- Vista de job:
  - muestra `size_bytes` por paso,
  - preview de `output_data` con boton "ver completo" (lee `job_step_outputs`).

### 6.5 No hay endpoint HTTP obligatorio para agentes

El GET `jobs/{id}/steps/{step_code}/output` deja de ser parte de la ruta principal. Puede existir como endpoint **interno** para la consola (auth Operator), pero **ningun agente tiene que llamarlo** para funcionar.

## 7. Compatibilidad y migracion sin romper nada

- **Fase 0**: deploy schema (alter + tabla nueva). Codigo igual. Defaults compatibles.
- **Fase 1**: codigo dual-write: escribe `output_data` en `job_step_outputs` **y** sigue escribiendolo en `sequence_responses.output_data`. Jobs existentes siguen funcionando.
- **Fase 2**: `_build_dispatch_payload` lee `depends_on_*`. Si estan vacios, comportamiento legacy (incrusta todo). Si estan definidos, aplica embed filtrado + proyeccion. Produccion: actions criticas se migran una a una.
- **Fase 3**: UI permite configurar dependencias. Catalogos se actualizan.
- **Fase 4**: `_record_sequence_outcome` deja de escribir `output_data` crudo en `metadata`. Los jobs antiguos ya estan poblados en `job_step_outputs`; jobs nuevos solo usan la tabla.

## 8. Que deliberadamente **no** hacemos en este ADR

- No introducir un DSL de orquestacion tipo BPMN o Step Functions. `agent_sequence` + `depends_on_*` es suficiente a medio plazo.
- No paralelizar pasos todavia. Si se hace, requiere `version int` en `jobs` con optimistic locking o mover a event sourcing. Otra ADR.
- No firmar payloads HMAC orquestador-agente. Otra ADR (seguridad).
- No cambiar `gate_responses` a una tabla propia; el volumen es acotado. Si algun gate devuelve blobs enormes, se resuelve con `gates_projection`.
- No obligar a los agentes a hacer ningun GET adicional. Embed por valor es la ruta primaria, por decision de producto.

## 9. Tabla resumen de decisiones

| Decision | Quien | Cuando |
|----------|-------|--------|
| Aprobar ADR v2 (embed primero) | Plataforma + Producto | Antes de Fase 0 |
| Definir `output_schema` por paso | Dueno del agente | Antes de Fase 2 |
| Definir `depends_on_*` para cada action con secuencia > 1 | Producto + Plataforma | Fase 3 |
| Politica de retencion `job_step_outputs` | Plataforma + Legal | Fase 1 |
| Activar validacion schema en callback | Plataforma | Fase 2 |

## 10. Referencias cruzadas

- Flujo actual: `flujo-router-prerequisitos-gates-secuencia.md`.
- Payload actual: `contrato-payload-dispatch-agente.md`, `ejemplo-json-body-agente-y-gates.md`.
- Modelo actual: `modelo-de-datos-erd.md`.
- Deuda historica: `../desarrollo/historial-arquitectura-mejoras-y-deuda.md`.

---

## A. Apendice - Variante `reference` para overflow (opcional)

Solo aplica si un paso especifico tiene outputs incontrolablemente grandes (por ejemplo >5 MB con assets base64) y el agente **no puede** recibirlos por valor. En ese caso, `inject_policy=reference` cambia la entrada en `previous[step_code]` a:

```json
{
  "status": "COMPLETED",
  "sequence_order": 2,
  "output_schema": "web_assets.v1",
  "output_ref": "/api/v1/jobs/{job_id}/steps/{step_code}/output",
  "output_size_bytes": 5213894,
  "summary": { "assets": 42 },
  "started_at": "...",
  "completed_at": "...",
  "duration_ms": 82000
}
```

Cuando un agente configurado para `reference` recibe esto, hace GET al endpoint con su `Authorization: Bearer`. Esto **no es la ruta principal**; es valvula de emergencia. Se documenta para que quede claro que existe si alguien la necesita, no como patron recomendado.

Alternativa preferida antes de usar `reference`: **proyeccion** (`previous_projection` o `gates_projection`) para reducir lo que viaja. O rediseñar el output del agente productor para que no devuelva el blob crudo.
# Golden example — Website brief (ATLAS input)

**Purpose of this file.** This is a *target brief shape*: what ATLAS wants to receive to produce a quality website. For web, the brief is the primary driver — ATLAS's multi-agent internal sequence mostly rearranges and styles what's already in the brief. Brief completeness sets the ceiling on output quality.

**Key difference vs. the post golden example:** for posts we shared a target *artifact* (what CF outputs); for web we share a target *brief* (what ATLAS consumes). Web is too large to treat the final site as golden; the brief is the leverage point.

MARKETER does not invent any of these fields. They arrive from ROUTER. MARKETER's job for `create_web` / `edit_web` is strategic framing *on top of* this brief — narrative hierarchy, what to lead with, where to focus — not re-authoring facts.

**Format:** Landing page
**Client:** Fontaneria Rodriguez (Barcelona plumber)
**Site type:** landing_page
**Completeness tier:** full (`_expected_score: 100`)

---

## The full brief (what ROUTER delivers to ATLAS)

```json
{
  "_tier": "full",
  "_expected_score": 100,
  "_note": "Golden run: all scoring fields + brand assets + style preferences + reference URLs.",

  "name": "Fontaneria Rodriguez",
  "industry": "plumbing",
  "business_type": "plumbing",

  "city": "Barcelona",
  "region": "Cataluna",
  "country": "Spain",
  "address": "C. de Mallorca, 112",
  "postal_code": "08036",
  "lat": 41.3917,
  "lng": 2.1531,
  "service_area_radius_km": 30.0,

  "phone": "+34 612 345 678",
  "email": "info@fontaneriarod.es",
  "website_current": "https://fontaneriarod.es",
  "social_profiles": {
    "instagram": "https://instagram.com/fontaneriarod",
    "facebook": "https://facebook.com/fontaneriarod"
  },

  "services": [
    "Reparacion de averias urgentes",
    "Instalacion de tuberias",
    "Fontaneria industrial",
    "Mantenimiento de calderas",
    "Instalacion de sistemas de calefaccion",
    "Deteccion de fugas con camara"
  ],

  "description": "Fontaneria Rodriguez lleva mas de 10 anos resolviendo averias e instalaciones en Barcelona y area metropolitana. Servicio urgente 24h, presupuesto sin compromiso.",
  "brief": "Quiero destacar el servicio de urgencias 24h y la camara de deteccion de fugas — eso nos diferencia. El tono debe ser de confianza, como el fontanero del barrio de toda la vida pero con tecnologia moderna.",

  "year_established": 2013,
  "team_size": "2-5",

  "review_count": 47,
  "review_avg": 4.8,
  "reviews": [
    { "source": "google", "rating": 5.0, "text": "Vinieron en menos de una hora. Arreglaron la fuga rapidamente y a buen precio.", "author": "Carmen L." },
    { "source": "google", "rating": 5.0, "text": "Muy profesionales. Explicaron todo antes de empezar y no hubo sorpresas en el presupuesto.", "author": "Marcos T." },
    { "source": "google", "rating": 4.0, "text": "Buen trabajo en la instalacion del bano nuevo. Algo de retraso pero el resultado final fue excelente.", "author": "Ana P." },
    { "source": "google", "rating": 5.0, "text": "Los llame a las 2 de la manana por una fuga y en 45 minutos estaban en casa. Impresionante.", "author": "David M." },
    { "source": "google", "rating": 5.0, "text": "Detectaron la fuga con camara sin tener que romper nada. Ahorro enorme de tiempo y dinero.", "author": "Isabel V." }
  ],

  "photos_urls": [
    "mock://rodriguez/equipo-trabajo.jpg",
    "mock://rodriguez/instalacion-caldera.jpg",
    "mock://rodriguez/camara-deteccion.jpg",
    "mock://rodriguez/reforma-bano.jpg"
  ],

  "style_preferences": ["Limpio y profesional", "Transmitir rapidez y fiabilidad", "Fotos reales del trabajo, no stock"],
  "style_dislikes": ["Nada demasiado corporativo", "Sin colores demasiado frios"],
  "reference_urls": ["https://sincro.es"],
  "tone_preferences": ["cercano", "directo", "de confianza"],
  "special_requests": ["El numero de telefono debe aparecer muy visible en la cabecera"],

  "brand_completeness": "partial",
  "brand_primary_color": "#1A3A5C",
  "brand_accent_color": "#E8A020",
  "brand_voice": "El fontanero de confianza del barrio, pero con herramientas del siglo XXI.",
  "site_type": "landing_page"
}
```

---

## Analysis

1. **Web brief is structurally richer than post brief.** Reviews (structured), services list, geocoordinates + service radius, team size, year established, reference URLs, style likes/dislikes, special requests, brand_completeness meta-field, explicit brand colors. Our current `FlatBrief` (§5.2) was modeled on the Plinng post onboarding brief and does not cover these. For web actions, the normalizer needs to tolerate a broader superset of brief fields.

2. **`_tier` / `_expected_score` / `brand_completeness` are grading signals.** They indicate ATLAS has a brief-quality scoring system. MARKETER can use `brand_completeness` as an input signal (partial/full) and raise `brief_field_missing` warnings more aggressively when weaker.

3. **`site_type` changes scope radically.** `landing_page` vs `multi_page` reshapes `website_guidance.narrative_hierarchy` and `recommended_section_focus`. Landing = one-page conversion funnel. Multi = multi-section informational site. MARKETER must read this and adapt.

4. **`special_requests` are hard constraints.** "Phone visible in header" is non-negotiable. MARKETER should reinforce these in `website_guidance` — never reinterpret, never drop.

5. **`reference_urls` are inspiration, not mandates.** MARKETER can draw positioning/tone cues, not copy or layout.

6. **`reviews` are structured and exploitable.** MARKETER can direct *which themes to amplify* (response time, technology, transparency) without quoting them verbatim. Quoting is craft; thematic direction is strategy.

7. **`brief` field ≈ `client_request.description`.** The freeform user wish in this golden maps to what ROUTER carries in `payload.client_request.description`. When both are present, they should be reconciled (likely identical; if divergent, `client_request.description` wins since it's the live request).

---

## What MARKETER's enrichment would look like for this site

```json
{
  "schema_version": "1.0",
  "task_interpretation": "Landing page para fontaneria local en Barcelona. El usuario pide destacar urgencias 24h y la camara de deteccion como diferenciadores principales.",
  "objective": "Generar llamadas entrantes y solicitudes de contacto desde clientes con averias o reformas en Barcelona y area metropolitana.",
  "key_message": "Fontaneria de barrio de confianza con tecnologia moderna — rapidez cuando importa, precision cuando toca.",
  "supporting_points": [
    "Urgencias 24h como diferenciador principal, con testimonios que lo prueben",
    "Camara de deteccion como evidencia de modernidad y ahorro para el cliente",
    "Mas de 10 anos de trayectoria local + equipo pequeno = trato directo",
    "Transparencia en presupuesto, sin sorpresas"
  ],
  "audience_fit": "Propietarios e inquilinos del area metropolitana de Barcelona con una averia urgente o una reforma pendiente. Sensibles a confianza local y velocidad de respuesta.",
  "tone_guidance": "Cercano, directo, de confianza — fontanero del barrio pero con lenguaje moderno. Sin corporativismo. Catalan-friendly Spanish.",
  "recommended_angle": "Honestidad operativa: 'te cogemos el telefono, llegamos pronto, te decimos el precio antes de empezar'. Apoyarse en pruebas concretas (tiempo de respuesta real, 4.8 estrellas, 47 resenas).",
  "visual_guidance": {
    "use_brand_material": true,
    "recommended_asset_urls": [
      "mock://rodriguez/camara-deteccion.jpg",
      "mock://rodriguez/equipo-trabajo.jpg"
    ],
    "avoid_asset_urls": [],
    "reason": "La camara visualiza el diferenciador tecnologico en hero o seccion de servicios. La foto del equipo humaniza la pagina de confianza. La caldera es menos distintiva y puede ir como asset secundario."
  },
  "edit_guidance": null,
  "website_guidance": {
    "narrative_hierarchy": [
      "Urgencia 24h + telefono prominente (hero)",
      "Diferenciador tecnologico: camara de deteccion",
      "Prueba social: testimonios, 4.8 estrellas, 47 resenas",
      "Servicios completos (urgencias, calderas, calefaccion, industrial)",
      "Cobertura y area de servicio (Barcelona + 30km)",
      "CTA final de contacto"
    ],
    "recommended_section_focus": [
      "Hero debe responder a 'tengo un problema ahora' — telefono muy visible (constraint del usuario)",
      "Testimonios priorizar los que mencionan tiempo de respuesta (David M., Carmen L.) y camara (Isabel V.)",
      "Servicios liderar con urgencias y camara; fontaneria industrial secundaria en landing",
      "Reforzar transparencia de presupuesto — dos testimonios la mencionan"
    ]
  }
}
```

What MARKETER does *not* produce here: hero copy, CTA button wording, exact testimonial selection logic, section layout, color application, typography. ATLAS's internal sequence owns all of that.

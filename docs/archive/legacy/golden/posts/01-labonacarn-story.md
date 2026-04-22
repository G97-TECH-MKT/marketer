# Golden example — Post (Instagram Story)

**Purpose of this file.** This is a *target artifact*: what CONTENT_FACTORY should produce for a story post when it receives (a) Brand DNA from ROUTER and (b) strategic direction from MARKETER. Use it as a reference when calibrating MARKETER's prompt depth. MARKETER does **not** produce the copy, the visual description, or the brand DNA below — those belong to CONTENT_FACTORY (craft) and to the brief (brand DNA). MARKETER produces the direction that makes this kind of output possible.

**Format:** Instagram Story
**Client:** La Bona Carn (Girona-based butcher)
**Date generated:** 16 d'abril del 2026, Diumenge
**Title:** Repartiment a domicili — De l'obrador a la teva porta

---

## Post proposal (what CF outputs)

### 🎯 Objectiu

Conversió directa a la botiga en línia i recordatori de la proposta de valor logística (comoditat).

### ⚖ Raonament estratègic

Les stories són perfectes per generar urgència i facilitar la conversió mitjançant un enllaç directe. Tanca el cicle de la setmana recordant que tot el que s'ha vist (l'obrador, la botifarra, la recepta) pot ser a casa seva aquesta mateixa tarda sense esforç, ideal per al target de famílies ocupades.

### ✨ Notes d'estil visual

Ús del paper kraft i el segell grana per reforçar el branding en l'embalatge. Llum de tarda daurada que contrasta temporalment amb la llum matinal del Post 1.

### 🔗 Connexió narrativa

Tanca el cercle narratiu de la setmana: vam començar a l'obrador a trenc d'alba (Post 1) i vam acabar a la porta del client a la tarda (Post 4), demostrant amb fets la promesa de la marca.

### 🖼 Descripció visual de la imatge

Foto en primera persona (POV) lliurant un paquet embolicat en paper kraft tradicional amb un adhesiu del logo de La Bona Carn (segell grana) a un client a la porta de casa seva. El repartidor porta un somriure (es veu de perfil) i el fons és una porta de fusta càlida o entrada típica de Girona. Link sticker interactiu prominent.

### 📝 Copy per a la imatge

> Aquest matí ho preparàvem a l'obrador i aquesta tarda ja és a casa teva per sopar. 🚐 💨
> Fes la teva comanda ara i gaudeix del plaer de menjar bé sense moure't de casa. T'ho portem a l'hora que triïs! 👇
> www.labonacarn.com

---

## Brand DNA (what ROUTER sends)

Everything below is sent by ROUTER in the brief. CONTENT_FACTORY uses it directly. MARKETER reasons *with* it but never re-invents it.

### Colors

- `#6ABF2E`
- `#1A1A1A`
- `#FFFFFF`

### Typography

Poppins, in bold and in regular/thin.

### Visual tone

**Positioning:** premium accessible — quality you eat every day, not a special occasion luxury.

**Design style:** clean and photography-forward. The image takes 70–80% of the frame. Text is minimal — one short line, bold, white or black depending on the background. The green `#6ABF2E` appears only as an accent: a small tag, a highlight on a word, or the CTA. No gradients, no textures, no filters on the photos. White space is used generously. The logo sits small in a corner. Typography is a bold rounded sans-serif, same family as the logo — never decorative, never scripted. Composition is simple: product centered or slightly off-center, text anchored to the bottom or top edge.

The green is the only thing that makes it recognizably theirs. Use it consistently and sparingly.

### Brand voice

- **Language:** Catalan, always
- **Tone:** close and familiar, like the trusted butcher you've known for years. Honest, no fluff.
- **Values to surface in every post:** local origin (Girona), zero additives, daily preparation, no intermediaries, traceability.

---

## What MARKETER's enrichment would have looked like for this story

(Reference reconstruction, not canonical. Shows the depth MARKETER should reach so CF can arrive at the target above.)

```json
{
  "schema_version": "1.0",
  "task_interpretation": "Cierre de la semana de contenidos con un story orientado a conversión directa, aprovechando la promesa logística (de l'obrador a casa el mateix dia).",
  "objective": "Empujar conversión directa en la tienda online apoyándose en la comodidad logística como último golpe de la semana.",
  "key_message": "Reforzar que la propuesta de valor logística cierra el cercle narratiu: lo que s'ha vist aquest matí a l'obrador pot sopar-se a casa aquesta tarda.",
  "supporting_points": [
    "Comoditat com a palanca de conversió per a famílies ocupades",
    "Tancament narratiu de la setmana (matí a l'obrador → tarda a la porta)",
    "Embalatge kraft + segell grana com a continuïtat de marca"
  ],
  "audience_fit": "Famílies ocupades del target habitual de la marca, que valoren qualitat però necessiten facilitat.",
  "tone_guidance": "Proper, de carnisser de confiança. Honest, sense floritures. Català, sempre.",
  "recommended_angle": "Urgència positiva + tancament de cicle: 'ho has vist aquest matí, tingues-ho aquesta tarda'. Story com a format natural per a crida a l'acció amb link sticker.",
  "visual_guidance": {
    "use_brand_material": true,
    "recommended_asset_urls": ["<url-del-paquet-kraft-o-equivalent>"],
    "avoid_asset_urls": [],
    "reason": "L'embalatge kraft amb segell grana reforça el branding al moment de l'entrega; llum de tarda daurada per contrastar amb la llum matinal del primer post de la setmana."
  },
  "edit_guidance": null,
  "website_guidance": null
}
```

Notice what MARKETER does *not* produce: the copy, the specific hashtags, the exact CTA phrasing, the pixel-level composition. Those come from CF once it has brand DNA + this direction.

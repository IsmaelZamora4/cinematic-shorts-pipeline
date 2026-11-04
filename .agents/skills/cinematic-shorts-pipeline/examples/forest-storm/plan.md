# Simulación B · El refugio del tronco

**Solo planificación ficticia.** Sin imágenes, audio, voz, modelos ni renders.

## Encargo y premisa

Clip vertical de supervivencia, 6 s, una excursionista atrapada por una tormenta en bosque montañoso. **Logline:** cuando un rayo derriba un árbol junto a su ruta, la excursionista usa el destello para localizar un refugio bajo una pared de roca.

## Estructura narrativa

0–2 s: lluvia y ráfagas vuelven invisible la ruta. 2–4 s: trueno y árbol caído elevan el peligro y bloquean el sendero. 4–6 s: el destello revela una cornisa; la excursionista se cubre allí antes de la siguiente ráfaga. Resolución breve: encuentra resguardo inmediato, tormenta continúa.

## Dirección artística

Low-poly realista y sobrio; troncos, helechos y suelo mojados con formas irregulares; ropa impermeable adulta, mochila ceñida y postura pesada contra el viento. Paleta fría de tormenta con un destello blanco breve como fuente motivada; luz ambiente azul tenue para separar siluetas. Evitar lluvia como patrón gráfico uniforme.

## Plan técnico

30 FPS, 180 fotogramas, tres planos de 60. Candidato Blender/Eevee para lluvia y viento controlados o Remotion/R3F si toda acción se deriva del fotograma; elección pendiente de prueba. Mantener partículas escasas por GPU de 4 GB; empezar con una imagen fija antes de simular lluvia. Audio de tormenta solo planificado, no creado.

## Control de calidad

- Automático: validar timeline contiguo, tipos, duración, voz/captions y JSON estricto.
- Fotogramas fijos: lectura de silueta bajo lluvia, árbol caído, cornisa y posición protegida; destello no quema el cuadro.
- Humano: causa/efecto del peligro y hallazgo, geografía del sendero, plausibilidad de equipo y legibilidad en teléfono.
- Temporal: dirección del viento/lluvia, caída del árbol, reacción corporal, ritmo de destellos y continuidad exigen secuencia; un still no los demuestra.

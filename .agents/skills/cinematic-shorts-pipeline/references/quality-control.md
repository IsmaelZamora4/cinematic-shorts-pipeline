# Control de calidad y puertas

Registra cada comprobación como **aprobada**, **fallida**, **sin revisar** o **no aplicable**. Un plan no demuestra que una implementación funcione.

## Puertas de planificación

- **Encargo:** plataforma/formato, duración, idioma, tono, entregables, restricciones y autorización anotados.
- **Historia:** objetivo, obstáculo, riesgo, giro y final claros; hechos separados de la ficción.
- **Estética/recursos:** reglas visuales coherentes; fuente/licencia/compatibilidad exactas comprobadas o incertidumbre declarada.
- **Storyboard — automático:** JSON, campos, enums/rangos opcionales, IDs, tiempos, captions y cobertura total pasan validadores.
- **Storyboard — intención cinematográfica:** por plano se puede identificar primer foco, acción, información nueva, fuente de luz y razón del corte; continuidad de eje, dirección y mirada no se contradice. Esto aprueba el plan escrito, no su imagen.
- **Animación — intención:** rig/status/controles requeridos declarados; beats cubren el plano sin huecos; poses, peso, apoyos, root motion, mirada, manos/props y entradas/salidas están descritos; continuidad textual entre planos revisada. Un rig `unselected` o `unverified` mantiene la implementación bloqueada.
- **Animación — automático:** el contrato opcional tiene tipos/enums válidos, beats relativos contiguos dentro de cada escena y controles/estados reconocidos. No prueba rig, movimiento o continuidad perceptual.
- **Storyboard — still:** solo se aprueba con fotogramas revisados en viewport 9:16 a tamaño de teléfono; foco, geografía, perspectiva, luz y pistas son legibles.
- **Storyboard — movimiento:** solo se aprueba con secuencia reproducida; eje, dirección de pantalla, movimiento de cámara y continuidad temporal comprobados.
- **Actuación — movimiento:** solo se aprueba tras reproducir secuencia; peso/apoyo, root motion, anticipación, mirada, contactos de manos/props y estados de entrada/salida se evalúan entre cortes. Still y JSON no lo sustituyen.
- **Plan técnico:** motor/versión y ruta de recursos verificados; audio, subtítulos y perfil descritos; sin dependencias externas no autorizadas.

## Puertas de producción (solo si se solicita producir)

- Revisa fotogramas fijos a tamaño móvil para cada plano antes del render completo.
- Comprueba recortes geométricos, siluetas, materiales/texturas, exposición, sombras, jerarquía focal, continuidad de cámara y legibilidad del hallazgo en stills por plano.
- Reproduce la secuencia para revisar dirección de pantalla, eje/mirada, movimientos motivados, empalmes, acción y continuidad temporal. No apruebes movimiento desde stills.
- Revisa en secuencia apoyos y transferencia de peso, deslizamiento, raíz, anticipación, seguimiento de mirada, contacto/agarre/liberación de manos y respuesta de props; compara pose y estado a través de cada corte.
- Verifica duración real de voz y subtítulos; escucha inteligibilidad, picos/clipping y conflictos de mezcla.
- Mide tiempo de render y memoria en la máquina destino; limita concurrencia y calidad inicial cuando sea necesario.
- Tras el render solicitado, inspecciona fotogramas representativos y reproduce el archivo; valida lectura, dimensiones, FPS, duración, audio y subtítulos. Informa comandos y resultados observados.

Los scripts incluidos validan el empaquetado de la skill, los datos del storyboard, el DAG del pipeline y manifiestos existentes. No ejecutan etapas de producción, renderizan, inspeccionan calidad audiovisual, verifican licencias ni juzgan la narrativa.

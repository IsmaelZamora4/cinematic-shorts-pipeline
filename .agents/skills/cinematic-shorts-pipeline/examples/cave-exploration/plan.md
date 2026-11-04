# Simulación A · La cuerda que termina

**Planificación ficticia únicamente.** No se generaron imágenes, audio, modelos, renders ni video; no se inspeccionaron stills ni reproducción.

## Encargo y premisa

Short vertical 9:16 de misterio, 6 s, exploradora adulta. **Logline:** una exploradora encuentra una cuerda recién cortada mientras el agua sube y debe elegir una repisa estrecha para salir.

## Arco

0–2 s: la linterna revela agua nueva y establece ruta/peligro. 2–4 s: cuerda cortada cambia la lectura de una cámara aparentemente vacía. 4–6 s: agua alcanza una marca; la exploradora elige mirar hacia la repisa. Cierre: la amenaza sigue activa y su origen permanece desconocido.

## Dirección artística

Low-poly serio, proporciones adultas, ropa funcional, cueva asimétrica y sedimento. Paleta de roca fría y ámbar restringido al haz. Una fuente motivada; rebote tenue conserva silueta y pista. La cámara compone en 9:16 desde el inicio; no se añaden detalles luminosos sin causa narrativa.

## Desglose cinematográfico por plano

El punto focal se expresa como proporción aproximada del cuadro (`x`,`y`, origen arriba-izquierda); no representa posición del mundo. El eje nombrado describe la relación exploradora-interior. Se revisan intención y continuidad en texto, no apariencia.

| Tiempo / plano | Primera atención | Acción | Información nueva | Luz narrativa | Eje, dirección y conexión |
| --- | --- | --- | --- | --- | --- |
| 0–2 s · `entrada` | Mancha del haz sobre la línea de agua, en tercio inferior/derecho | La exploradora entra y frena al alcanzar el sendero mojado | El agua cubre una ruta dentro de un lugar que parece abandonado | Linterna frontal; rebote discreto desde roca próxima | Establece `exploradora-hacia-interior`; se desplaza alejándose de cámara. El movimiento del haz hacia pantalla derecha motiva el corte al extremo de cuerda sin invertir eje. |
| 2–4 s · `indicio` | Fibras del extremo cortado; mano detenida sirve de guía | La exploradora se para; paneo corto acompaña mirada y mano sin tocar la pista | Alguien cortó la cuerda recientemente; lugar no está tan vacío como parecía | La misma linterna acompaña la mirada; relleno conserva mano, cuerda y pared húmeda | Mismo eje; mano/mirada hacia derecha. Tras leer la pista, corte a su reacción y a la ruta que debe elegir. |
| 4–6 s · `decision` | Giro/mirada hacia repisa; agua en la marca mantiene presión | La exploradora gira hacia la ruta y decide avanzar | Repisa ofrece ruta, pero el agua sigue alcanzando el sendero | La linterna sigue el giro; el haz revela repisa sin alumbrar el fondo completo | Mismo eje y mirada a derecha. Último plano: sostener la elección y terminar antes de mostrar el destino; no hay plano siguiente. |

## Desglose de actuación por plano

Offsets relativos al inicio del plano. Cada beat describe estado de entrada/salida y cubre todo el intervalo; “sostener” también es acción planificada. La raíz se describe por intención/dirección de pantalla, sin distancias ni coordenadas de mundo.

| Plano / beats | Pose, anticipación y peso | Apoyos / root motion | Mirada | Manos y prop | Empalme |
| --- | --- | --- | --- | --- | --- |
| `entrada` 0–42 | Marcha cautelosa; cambiar peso antes del paso de frenado; torso desacelera sobre el pie de apoyo | Izquierdo sostiene mientras derecho oscila; raíz viaja hacia el interior | Línea de agua; termina mirando abajo | Sin acción de manos | Plano inicial; la trayectoria ya viene orientada al interior |
| `entrada` 42–60 | Postura se asienta; sin anticipación nueva | Ambos pies plantados; raíz estacionaria | Sube de la línea de agua hacia la cuerda a pantalla derecha | Sin acción de manos | Sale equilibrada; `indicio` recibe la misma mirada |
| `indicio` 0–26 | Ojos llegan antes que cabeza; torso orienta hacia el hallazgo | Ambos pies plantados; raíz estacionaria | Fibras cortadas, a pantalla derecha | Manos recogidas | Recibe mirada del plano anterior; prepara alcance sin mover base |
| `indicio` 26–60 | Mano izquierda alcanza y frena antes de tocar; peso permanece sobre la base | Dos pies plantados; raíz estacionaria | Mantiene foco en el corte | Interacción `approach`; cuerda no se mueve, no hay contacto | Sale con mano cerca y mirada fija; plano siguiente levanta la cabeza sin invertir eje |
| `decision` 0–20 | Mirada sale primero de la cuerda; giro del cuerpo sobre una base estable | Pie derecho sostiene, izquierdo libera el giro; raíz hace giro en sitio | Repisa a pantalla derecha | Mano se retira antes del contacto; no hay estado `release` porque nunca agarró la cuerda | Recibe mirada/mano del plano anterior; conserva eje |
| `decision` 20–45 | Cargar apoyo antes de avanzar; torso acompaña el paso | Izquierdo sostiene, derecho oscila; raíz viaja hacia el interior | Borde de la repisa | Sin acción de manos | Paso continúa el giro, sin salto de pose |
| `decision` 45–60 | Completar transferencia y sostener decisión | Ambos pies plantados; raíz estacionaria | Mantiene destino fuera de alcance | Sin acción de manos | Plano final; sin toma siguiente |

**Rig:** `unselected` en los tres planos. Requisito conceptual: bípedo adulto con controles de raíz, mirada, pies IK, manos IK y contacto de prop. Sin nombres de hueso, archivo, licencia ni compatibilidad afirmados; se verifican con el recurso exacto en un bloque posterior.

## Problemas del primer planteamiento y decisiones

- **Negro amplio sin lectura:** plano 1 podía dejar figura pequeña rodeada de roca oscura. **Decisión:** primer foco sobre agua iluminada; mantener sendero y silueta legibles, reservar negro al túnel que funciona como destino incierto.
- **Mirada sin objeto claro:** paneo del plano 2 no definía con precisión su llegada. **Decisión:** detenerlo sobre las fibras cortadas; la mano y la mirada conducen, no tapan la cuerda.
- **Pista débil:** la cuerda podía perderse contra pared húmeda. **Decisión:** asignarle punto focal propio, silueta despejada, luz rasante y escala de plano medio.
- **Luz sin motivación comprobable:** la frase “rebote tenue” no ubicaba su causa. **Decisión:** linterna se mantiene fuente principal; nombrar pared próxima como origen del rebote y verificarlo en still si se autoriza producción.
- **Jerarquía 9:16 incierta:** la composición anterior solo decía “vertical”. **Decisión:** reservar primer término/lower frame para la pista o agua, espacio medio para acción y fondo para ruta; revisar cara, mano, pista y zona de interfaz en el encuadre final.
- **Continuidad solo general:** no había relación de eje reutilizable ni dirección asignada. **Decisión:** repetir `axis_id`, declarar avance y mirada a la derecha; cambio de lado requeriría giro o plano neutral visible.
- **Movimiento de personaje sin fase:** “entra” no aclaraba apoyo, transferencia ni parada. **Decisión:** dividir en viaje y asentamiento con offsets relativos, pies declarados y root motion coherente con el eje.
- **Alcance podía confundirse con contacto:** se mencionaba mano y cuerda sin estado físico. **Decisión:** usar `approach`, detener antes de tocar y declarar respuesta inmóvil de la cuerda; el plano final retira la mano en estado `none`, no `release`.
- **Sin continuidad de pose entre cortes:** no se indicaban estados recibidos/entregados. **Decisión:** registrar mirada, equilibrio, pies y mano en entrada/salida por plano; el script valida la ficha individual, la continuidad perceptual sigue siendo humana/temporal.

## Estado de revisión

- **Automático:** estructura y cronología susceptibles de validación por script; sin afirmar aprobación cinematográfica.
- **Animación automática:** el contrato valida campos, enums y cobertura contigua de beats relativa al plano; no comprueba rigs, peso, contacto o naturalidad.
- **Intención escrita:** cada plano identifica primera atención, acción, revelación, fuente de luz y corte siguiente; plano final declara cierre sin siguiente toma.
- **Fotograma fijo:** sin revisar; no hay imágenes.
- **Secuencia:** sin revisar; no hay reproducción. Movimiento de cámara, eje percibido, exposición, lectura móvil, apoyo, peso, raíz, mirada, contacto y continuidad de actuación no están probados.

## Plan técnico acotado

30 FPS, 180 fotogramas, tres planos de 60. Blender o Remotion/R3F quedan como candidatos sin implementar ni escoger. Nada aquí prescribe coordenadas 3D o valores de lente calibrados; la futura escena debe verificar perspectiva, escala y luz. Cualquier prueba visual requiere autorización de producción.

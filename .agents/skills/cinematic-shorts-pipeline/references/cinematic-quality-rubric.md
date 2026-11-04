# Rúbrica de calidad cinematográfica

Registra evidencia por plano; separa validación de datos, inspección de stills y juicio temporal. No conviertas intención escrita, código JSON válido o una captura única en una afirmación de calidad audiovisual.

## Procedencia del piloto

Observaciones comunicadas por el usuario a partir de capturas estáticas del piloto anterior. Las capturas no están incluidas ni se inspeccionaron de forma independiente durante esta revisión:

- Espacio negro sin información narrativa.
- Personaje visible, pero punto de interés poco claro.
- Objetos importantes con poco destaque.
- Luz poco motivada/insuficiente y jerarquía visual débil en 9:16.
- Capturas fijas no permiten juzgar fluidez, apoyos, actuación ni continuidad temporal.

Son hallazgos cualitativos para orientar plan y revisión; no métricas, mediciones ni resultado de reproducción.

## 1. Comprobaciones automáticas

El validador puede probar sintaxis JSON, campos base, IDs, intervalos, duración, captions, `cinematography`, el bloque opcional `animation` y las anclas opcionales de `narrative_timing`: tipos, referencias a escenas, orden, límites de audio y procedencia declarada. Puede comprobar forma y rango, no si existe la grabación o si la ancla coincide con ella; tampoco si un rig existe o corresponde, si hay peso, apoyo, mirada o contacto convincente, ni si dos planos enlazan en movimiento.

**Estado registrable:** pasa/falla con comando, archivo y error exacto. El paso estructural nunca aprueba cinematografía.

## 2. Revisión de fotograma fijo

Inspecciona cada plano en el encuadre nativo 9:16 y tamaño de teléfono previsto. Registra ruta/fotograma, tamaño mostrado y evidencia. Marca cada criterio `claro`, `parcial`, `falla` o `sin revisar`; usa `N/A` con motivo.

| Criterio | Claro | Parcial | Falla |
| --- | --- | --- | --- |
| Primera atención | Punto focal identificado de inmediato; coincide con `subject_or_clue` | Se entiende tras buscar | Mirada va a oscuridad/adorno distinto o no hay foco |
| Acción y geografía | Se entiende qué hace el sujeto y dónde está la pista/destino | Acción entendible, relación espacial débil | Se requiere explicación externa |
| Composición vertical | Sujeto y pista conservan escala, separación y zona segura | Uno se lee con esfuerzo o compite con interfaz | Recorte, borde o superposición oculta lectura esencial |
| Jerarquía | Valor, silueta, escala y líneas de mirada priorizan información planeada | Punto focal visible, pero competencia alta | Personaje/pista no destaca |
| Luz narrativa | Fuente inferible y dirección coherente; sujeto/pista visibles con detalle útil en sombras | Fuente plausible, contraste o detalle dudosos | Luz no motivada o acción/pista desaparece |
| Perspectiva/ángulo | Tamaño, ángulo y perspectiva sirven a escala/intención sin deformación accidental | Intención legible con distorsión o escala dudosa | Encuadre/ángulo cambia lectura sin motivo |
| Información despejada | Rostro, manos, pista y acción que deben verse no quedan tapados | Interferencia leve | Elemento narrativo queda oculto |
| Pose, mirada y props | Silueta, objetivo de mirada y relación espacial mano/prop son legibles | Intención entendible con obstrucción menor | Pose tapa acción, objetivo o contacto declarado |
| Hook y premisa | Personaje/acción y pregunta central se entienden en la primera imagen o beat | Se entienden tras buscar una pista | El primer beat promete una premisa distinta o no presenta situación |
| Función de plano | Acción y pista legibles; el encuadre sirve a su función narrativa | Se reconoce la función con esfuerzo | Plano repite, distrae o no contiene la información requerida |

No otorgues nota numérica global. Cita qué píxeles/relación del cuadro sustentan el estado; “campo declarado” no es evidencia visual.

## 3. Revisión en movimiento

Requiere una secuencia reproducida, no storyboard ni stills. Evalúa y registra por corte:

- Eje y dirección de pantalla: entrada, salida, desplazamiento y mirada mantienen geografía; anota dónde se establece un cambio.
- Movimiento de cámara: tiene disparador/destino legibles; velocidad y parada no compiten con acción.
- Continuidad: el objetivo del personaje, fase de acción, mano/prop, estado de luz, mirada, orientación y eje enlazan en cada corte; paneo o seguimiento no saltan.
- Actuación/mecánica: peso, anticipación, apoyos, root motion, mirada y manos tienen continuidad; contacto/agarre/liberación se sostiene y el prop responde según plan.
- Rig/retarget: control de raíz, pies, mirada, manos y prop comprobados en el recurso exacto; registro de rig/asset y defectos de deformación. El script no valida compatibilidad.
- Empalme entre planos: pose, equilibrio, dirección, mirada y estado mano/prop de salida coinciden con la entrada siguiente o tienen elipsis/cambio intencional documentado.
- Ritmo del corte: información nueva llega antes de la siguiente toma y el cierre conserva intención.
- Audio como reloj: compara cada ancla/corte con la grabación reproducida; revisa pausas, duración de frase y relación causal entre voz y visual. `measured` o `manually_reviewed` declarado en JSON no reemplaza escuchar y guardar evidencia.
- Progresión: reproduce sin detener; registra si objetivo, obstáculo, pista, giro y cierre derivan uno del otro, si el público puede seguir la causa y si cada corte llega cuando cambia la información.

Registra archivo reproducido, intervalo/corte, defectos y `comprobado`/`sin revisar`. Sin secuencia, estado temporal = `sin revisar`; no extrapoles a partir de una imagen.

## Revisión de intención antes de producir

En el plan escrito para cada plano contesta: qué mirar primero, qué acción ocurre, qué información nueva aparece, de dónde viene la luz y por qué corta al siguiente. Para animación, revisa rig/controles requeridos, beats/poses, anticipación, cambio de peso, pies, raíz, mirada, manos/prop, y estado de entrada/salida con el plano vecino. Esta pasada aprueba solo coherencia de planificación; imagen fija y movimiento permanecen `sin revisar`.

## Registro mínimo

```text
Plano / archivo / fotograma o intervalo:
Comando y resultado automático:
Primera atención / acción / información:
Fija (composición, jerarquía, perspectiva, luz, despeje): claro / parcial / falla / sin revisar + evidencia
Temporal (eje, dirección, mirada, movimiento, continuidad, ritmo): comprobado / sin revisar + evidencia
Actuación (rig, peso/apoyos, raíz, mirada, manos/prop, empalme): plan / still / secuencia + evidencia
Voz/cortes (fuente de reloj, pausa, ancla, alineación comprobada): sin revisar / comprobado + método/evidencia
Decisión/corrección:
Bloqueo restante:
```

No promedies fallos de lectura con fortalezas estéticas. Corrige primero lo que esconda acción, pista, geografía o dirección de mirada.

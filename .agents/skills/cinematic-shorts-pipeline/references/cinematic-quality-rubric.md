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

El validador puede probar sintaxis JSON, campos base, IDs, intervalos, duración, captions y campos opcionales de `cinematography`: enums conocidos, textos requeridos, listas y coordenadas focales numéricas `[0,1]`. Puede comprobar si faltan decisiones declaradas. No puede decidir si el objeto de atención se distingue, el eje tiene sentido, la luz parece motivada ni el corte funciona.

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

No otorgues nota numérica global. Cita qué píxeles/relación del cuadro sustentan el estado; “campo declarado” no es evidencia visual.

## 3. Revisión en movimiento

Requiere una secuencia reproducida, no storyboard ni stills. Evalúa y registra por corte:

- Eje y dirección de pantalla: entrada, salida, desplazamiento y mirada mantienen geografía; anota dónde se establece un cambio.
- Movimiento de cámara: tiene disparador/destino legibles; velocidad y parada no compiten con acción.
- Continuidad: gesto, luz, orientación y fase de acción enlazan; paneo o seguimiento no saltan.
- Actuación/mecánica: peso, anticipación, apoyos, mirada y manos tienen continuidad. La revisión de este bloque no sustituye una futura puerta de rigs/animación.
- Ritmo del corte: información nueva llega antes de la siguiente toma y el cierre conserva intención.

Registra archivo reproducido, intervalo/corte, defectos y `comprobado`/`sin revisar`. Sin secuencia, estado temporal = `sin revisar`; no extrapoles a partir de una imagen.

## Revisión de intención antes de producir

En el plan escrito para cada plano contesta: qué mirar primero, qué acción ocurre, qué información nueva aparece, de dónde viene la luz y por qué corta al siguiente. Comprueba `axis_id`, dirección de pantalla y mirada respecto a los planos vecinos. Esta pasada aprueba solo coherencia de planificación; imagen fija y movimiento permanecen `sin revisar`.

## Registro mínimo

```text
Plano / archivo / fotograma o intervalo:
Comando y resultado automático:
Primera atención / acción / información:
Fija (composición, jerarquía, perspectiva, luz, despeje): claro / parcial / falla / sin revisar + evidencia
Temporal (eje, dirección, mirada, movimiento, continuidad, ritmo): comprobado / sin revisar + evidencia
Decisión/corrección:
Bloqueo restante:
```

No promedies fallos de lectura con fortalezas estéticas. Corrige primero lo que esconda acción, pista, geografía o dirección de mirada.

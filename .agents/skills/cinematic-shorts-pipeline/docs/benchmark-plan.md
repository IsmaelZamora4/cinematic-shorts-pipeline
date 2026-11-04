# Protocolo de benchmarking

**Estado:** protocolo propuesto; no se han medido herramientas, tiempos, RAM/VRAM, calidad ni costes comparativos. No generar medios durante planificación. Cualquier prueba que produzca still, audio o render requiere autorización explícita.

## Propósito

Comparar versiones futuras de técnicas, skills o herramientas con el mismo encargo, hardware y rúbrica, para elegir una ruta local mantenible. No se usa un resultado anecdótico ni puntuaciones inventadas como línea base.

## Preparación reproducible

1. Fija el caso: usa una de las simulaciones de `examples/` y congela su brief, storyboard, duración y criterios de aceptación. Para comparaciones narrativas, presenta el mismo encargo original y oculta a revisores qué técnica produjo cada paquete.
2. Registra fecha, Windows build, CPU/RAM/GPU/VRAM detectados, drivers, espacio libre, Python, Blender, Node, Remotion, Three.js/R3F y versiones de cada skill/herramienta. Registra también ajustes, resolución, FPS, concurrencia y recursos exactos.
3. Verifica sin cambiar el entorno: versión instalada y comando de versión. Si una herramienta o dato no puede leerse localmente, registra **no medido**; no instales ni descargues para completar la ficha.
4. Ejecuta cada técnica desde el mismo estado limpio y con igual brief, límite de duración, activos y resolución. Repite el número de veces acordado y conserva logs/artefactos autorizados, comandos, timestamps y errores. No elimines salidas existentes sin autorización.
5. Separa medición estructural (JSON/validador), revisión ciega de imágenes cuando se autoricen stills, y evaluación temporal cuando se autorice una secuencia/video. No uses imágenes fijas como evidencia de naturalidad temporal.

Comandos de inventario/tiempo posibles en PowerShell, usando solo herramientas ya instaladas:

```powershell
Get-ComputerInfo -Property WindowsProductName,WindowsVersion,OsBuildNumber
Get-CimInstance Win32_Processor | Select-Object -ExpandProperty Name
Get-CimInstance Win32_ComputerSystem | Select-Object TotalPhysicalMemory
Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM,DriverVersion
python --version
node --version
Measure-Command { python -B scripts/validate-storyboard.py examples/cave-exploration/storyboard.json }
```

`AdapterRAM` puede no reflejar VRAM dedicada con precisión. Si `nvidia-smi` ya está instalado, registra muestras durante la ejecución con `nvidia-smi --query-gpu=timestamp,name,memory.used,memory.total --format=csv -l 1`; si no, usa una fuente local disponible o declara VRAM **no medida**. No instales herramientas para obtener estas cifras. Sustituye el comando de ejemplo de `Measure-Command` solo por la misma operación aprobada para cada candidato.

## Criterios y evidencia

| Dimensión | Evidencia/procedimiento | Registro |
| --- | --- | --- |
| Calidad narrativa | Revisores califican claridad de objetivo, obstáculo, causa/efecto, giro y cierre con la rúbrica; misma premisa para todos | Observaciones, desacuerdos, bloqueos; no inventar puntuación previa |
| Composición cinematográfica | Revisión ciega de cada still a igual tamaño y proporción; acción, escala, mirada, profundidad, pista y espacio seguro | Fotograma exacto y comentarios por plano |
| Iluminación | Revisión de fuente motivada, jerarquía, silueta, detalle en sombra/altas luces y lectura del hallazgo | Still, fuente de luz prevista y fallo observado |
| Naturalidad de animación | Solo con secuencia/video autorizado: peso, anticipación, apoyo/deslizamiento de pies, mirada, reacción y continuidad | Clip/fotogramas temporales y defectos; `no medido` si no se produjo |
| Compatibilidad Windows | Instalar no es parte de este protocolo. Verificar ejecución en instalación existente, requisitos, rutas, CLI, reinicio y errores | Windows build, versión, comando y resultado |
| RAM y VRAM | Medir en el mismo equipo durante la misma fase/carga; registrar pico y herramienta de medición. Task Manager/PerfMon/NVIDIA report si están disponibles | MB/GB pico, VRAM pico, método y límites; `no medido` si no disponible |
| Tiempo de ejecución | Cronometrar desde comando reproducible hasta artefacto/check terminado; separar setup, warm-up y ejecución | Segundos por repetición, mediana solo si hay suficientes repeticiones declaradas |
| Licencias/uso comercial | Revisar licencia exacta de código, modelo, textura, voz, fuente y dataset; atribución, redistribución y uso comercial | URL, texto/versión/fecha, obligaciones e incertidumbres |
| Costo adicional | Distinguir gratis, pago único, suscripción, créditos, hardware y tiempo de setup; confirmar precios vigentes | Moneda/unidad/fecha y supuesto; no imputar precio no verificado |
| Mantenimiento | Revisión de actividad/versionado, dependencias, docs, compatibilidad y coste de actualizaciones | Fecha de última versión observada, esfuerzo de actualización y riesgos |

## Reglas de decisión

- No mezclar datos de equipos, versiones, resoluciones o activos diferentes.
- No sustituir licencia dudosa por “gratis” ni afirmar permiso comercial sin fuente verificable.
- Mantener resultados faltantes como **no medidos**; fallos de instalación como resultados, sin instalar dependencias adicionales.
- Presentar trade-offs y evidencia por dimensión; no sumar una puntuación global sin pesos y umbrales aprobados previamente.
- Conservar scripts, configuración, prompt, versión, comando y artefactos necesarios para repetir la comparación.

## Ficha vacía

```text
Caso / técnica / versión:
Fecha / Windows / CPU / RAM / GPU / VRAM:
Dependencias y activos / fuentes / licencias:
Resolución / FPS / duración / concurrencia:
Comando / estado inicial / repeticiones:
Narrativa:
Composición:
Luz:
Animación temporal:
Compatibilidad Windows:
RAM pico / VRAM pico / método:
Tiempo por repetición:
Costo adicional:
Mantenimiento:
Fallos / no medido / incertidumbres:
Artefactos autorizados y rutas:
```

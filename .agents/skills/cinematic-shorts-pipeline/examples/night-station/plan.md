# La luz tras el sello

**Planificación narrativa original.** No se generaron ni inspeccionaron imágenes, audio, voz, modelos, renders o video. Todas las marcas de voz son estimaciones; no hay sincronía palabra a palabra.

## Brief y arco

- **Formato:** Short vertical 9:16, 12 s, 30 FPS, misterio sobrio de ficción.
- **Protagonista:** guardia adulto que revisa una estación clausurada.
- **Objetivo:** confirmar que el andén está vacío antes de cerrar la ronda.
- **Obstáculo/riesgo:** una lámpara encendida y huellas recientes contradicen el sello intacto de la puerta de servicio.
- **Pregunta:** ¿quién llegó a una puerta que nadie abrió?
- **Giro/cierre:** la lámpara se apaga; una luz responde desde el otro lado. El guardia deja el farol orientado hacia la rendija, sin abrirla.

## Beats y reloj de audio

La voz propuesta dura 10.8 s estimados. Si se graba, se reemplaza este reloj por la duración real y se reajustan pausas/cortes tras escucharla. El bloque `narrative_timing` contiene intervalos estimados a nivel de beat; acción y corte se referencian desde cada escena. No representa alineación de palabras.

| Tiempo estimado | Beat/voz | Acción visual y cambio de información | Corte/pausa |
| --- | --- | --- | --- |
| 0–1.8 s | Hook: «La estación debía estar vacía.» | Entra el guardia; un farol ya encendido en un banco contradice la premisa | Pausa hasta 3.0 s; corte al seguir su mirada |
| 3.0–6.6 s | «Pero el farol seguía tibio. Huellas mojadas llegaban a una puerta sellada.» | Comprueba calor, sigue las marcas; terminan en el sello exterior intacto | Pausa hasta 7.0 s; corte sobre la lectura del sello |
| 7.0–10.0 s | «La llama se apagó. Otra luz respondió desde dentro.» | El farol se extingue; aparece luz bajo la puerta. Decide dejarlo orientado hacia la rendija | Pausa final estimada hasta 10.8 s; cierre sin abrir la puerta |

Las captions son líneas de frase aproximadas, no cues sincronizados con voz grabada. Intervalos `[start_ms, end_ms)` y sin solape.

## Intención y QA

- **Primera atención:** farol visible en un andén vacío; después, guardia adulto y dirección de entrada.
- **Causa y efecto:** contradicción del farol provoca inspección; huellas conducen al sello; apagado precede la luz de respuesta; el guardia deja una señal para quien esté detrás.
- **Geografía:** banco a la izquierda, guardia recorre hacia la derecha, puerta al fondo/derecha. Mantener eje `guardia-puerta` y mirar hacia pantalla derecha hasta el cierre.
- **Luz:** farol práctico es fuente inicial; su apagado es cambio narrativo. La luz bajo la puerta motiva la última revelación. No se presume exposición, lectura ni sincronía visual reales.
- **Bloqueos de producción:** requiere stills verticales para revisar lectura de rostro, huellas, sello y rendija; requiere secuencia reproducida con grabación para comprobar duración, pausas, progresión, continuidad y momento de cortes.

## Resultado de planificación

La tabla hace revisable cada función de plano, primera atención, nueva información, acción, fuente y corte. Es una simulación de intención escrita: no constituye benchmark medido ni aprobación audiovisual.

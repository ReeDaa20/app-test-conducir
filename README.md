# AutoTest Pro

Aplicacion para practicar simulacros de examen de conducir.

## Que incluye

- Home con 100 simulacros de 30 preguntas.
- Temporizador de examen de 30 minutos.
- Guardado local de resultados en SQLite.
- Estado por test: sin hacer, apto o no apto.
- Revision de respuestas con tema, respuesta elegida, correcta y explicacion.
- Panel de preparacion con indicador de si estas listo para examen.
- Estadisticas por temas.
- Modo para repasar fallos.
- Test inteligente basado en fallos, temas flojos y preguntas nuevas.
- Importadores opcionales desde API, JSON o webs compatibles.

## Arranque

```powershell
python server.py
```

Despues abre:

```text
http://127.0.0.1:8000
```

La base de datos incluida en `data/autoescuela.sqlite` ya contiene el pool de simulacros.

## Regenerar pool externo

```powershell
python sync_dgt.py testsconducir-pool --tests 100 --questions-per-test 30
```

## Otros importadores

API via Apify:

```powershell
$env:APIFY_TOKEN="tu_token"
python sync_dgt.py apify
```

JSON propio o de otra API:

```powershell
python sync_dgt.py json --file preguntas.json --source mi-api
```

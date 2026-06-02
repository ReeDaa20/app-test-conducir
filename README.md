# AutoTest Pro

Primera versión de una aplicación para practicar tests de autoescuela.

## Qué incluye

- Frontend web con selección de test, preguntas, progreso, temporizador y revisión.
- Backend en Python con API local.
- Base de datos SQLite para guardar resultados.
- Banco inicial de preguntas de ejemplo.
- Conectores opcionales para importar preguntas desde API, JSON o webs gratuitas compatibles.

## Arranque

```powershell
python server.py
```

Después abre:

```text
http://127.0.0.1:8000
```

La base de datos se crea automáticamente en `data/autoescuela.sqlite` al arrancar.

## Sincronizar preguntas externas

La DGT ofrece tests web gratuitos con preguntas reales o similares y los actualiza regularmente, pero no publica una API oficial estable para apps. Por eso la app trabaja con proveedores intercambiables.

API vía Apify:

```powershell
$env:APIFY_TOKEN="tu_token"
python sync_dgt.py apify
```

Por defecto usa permiso B (`DGT_PERMIT_ID=1`) y castellano (`DGT_LANGUAGE_ID=1`).

Web gratuita compatible:

```powershell
python sync_dgt.py testsconducir --limit 10
```

Pool de 100 tests externos de 30 preguntas:

```powershell
python sync_dgt.py testsconducir-pool --tests 100 --questions-per-test 30
```

JSON propio o de otra API:

```powershell
python sync_dgt.py json --file preguntas.json --source mi-api
```

El JSON puede ser una lista o tener una clave `questions`. Cada pregunta debe incluir enunciado, opciones, respuesta correcta y, opcionalmente, imagen.

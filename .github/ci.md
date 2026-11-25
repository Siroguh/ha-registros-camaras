# CI, hooks y Pullfrog

## Comprobaciones

Python 3.14 y uv gestionan las herramientas de desarrollo mediante `uv.lock`:
Ruff 0.17.6 y pre-commit 4.7.0. No se instala Home Assistant ni se ejecutan tests.

```bash
sh scripts/setup-hooks.sh
sh scripts/check.sh
sh scripts/secrets.sh
```

La instalación configura hooks pre-commit y pre-push y descarga Gitleaks 8.30.1
con SHA-256 verificado (Linux x86_64/aarch64). Requiere uv, Git, curl, tar y
sha256sum. El entorno `.venv/` y los binarios `.local/` no se versionan.

- Pre-commit: escaneo de secretos preparados y Ruff lint/formato sobre archivos
  Python preparados. pre-commit aparta los cambios sin preparar durante el hook.
  Si Ruff modifica archivos, revísalos, prepáralos y repite el commit.
- Pre-push: lint, formato, compilación sintáctica y generación del ZIP.
- CI: instalación bloqueada por `uv.lock`, escaneo de la ascendencia completa
  de `HEAD` (incluidos los cambios de merges),
  comprobaciones estáticas, Hassfest y HACS en PR, `main`, tags y manualmente.

`sh scripts/check.sh` genera `dist/registros_camaras.zip`. La validación HACS no
solicita inclusión en el catálogo y las releases conservan su publicación actual.

## Instalación y flujo de Git

Instala primero los workflows en `main`, verifica CI y una revisión real de
Pullfrog, y aplica la protección **al final**. No se requieren revisores ni checks
que todavía no estén disponibles.

Configura [Pullfrog](https://pullfrog.com/console/Siroguh/registros_camaras_home_assistant):
verifica la instalación y activa revisiones automáticas, revisiones tras cada push,
aprobación de PRs y checks de ejecución/veredicto. Auto-merge permanece desactivado.
La conexión del proveedor puede heredarse de la cuenta `Siroguh`; las claves solo
pertenecen a secretos de GitHub/Pullfrog.

Después de verificarlo, usa ramas de trabajo y PRs. La protección de `main` exige
`check`, `hacs`, `hassfest`, una aprobación vigente, aprobación del último push por
otra identidad y conversaciones resueltas. Descarta aprobaciones antiguas y se
aplica a administradores; no permite force-push ni borrar `main`.

```bash
gh api --method PUT repos/Siroguh/registros_camaras_home_assistant/branches/main/protection \
  --input .github/branch-protection.json
```

Lee la configuración remota antes de reaplicar el archivo para conservar reglas
añadidas posteriormente. Un check `pullfrog` correcto no equivale a una revisión
aprobatoria. El veredicto `pullfrog-approval` se publica, pero la configuración
base exige la revisión de GitHub y los checks de CI.

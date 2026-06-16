import js from '@eslint/js'
import globals from 'globals'
import jsxA11y from 'eslint-plugin-jsx-a11y'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
      // F6.1 (auditoría 2026-06-10): a11y como gate de lint — los divs
      // clickeables sin rol/teclado pasaban silenciosos (caso Panel.tsx).
      jsxA11y.flatConfigs.recommended,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    rules: {
      // Deuda a11y PRE-auditoría: ~28 filas/cards clickeables sin teclado
      // (listas de eta/orders/chats/agents). Quedan en WARN para no bloquear
      // mientras se paga en su propia HU (hu-fe-a11y-interacciones); el resto
      // del recommended de jsx-a11y SÍ es error. Subir a error al cerrarla.
      'jsx-a11y/click-events-have-key-events': 'warn',
      'jsx-a11y/no-static-element-interactions': 'warn',
      'jsx-a11y/no-noninteractive-element-interactions': 'warn',
    },
  },
])

// Renamed from .eslintrc.js: package.json sets "type": "module", so Node treats a
// .js config as ESM and `module.exports` throws. The .cjs extension keeps it CommonJS.
//
// The `react-app` / `react-app/jest` presets were dropped. They come from
// eslint-config-react-app, a Create React App package that is not in
// devDependencies and not installed — this project moved to Vite. Extending a
// missing config made `npm run lint` fail outright. Everything referenced below
// is already in devDependencies; no new dependency is introduced.
module.exports = {
    root: true,
    extends: [
        'eslint:recommended',
        'plugin:@typescript-eslint/recommended',
        'plugin:react/recommended',
    ],
    parser: '@typescript-eslint/parser',
    plugins: ['react', '@typescript-eslint'],
    parserOptions: {
        ecmaVersion: 2020,
        sourceType: 'module',
        ecmaFeatures: {
            jsx: true
        }
    },
    settings: {
        react: {
            version: 'detect'
        }
    },
    env: {
        browser: true,
        es2020: true,
        node: true,
    },
    rules: {
        // Add custom rules here
        'react/react-in-jsx-scope': 'off', // Not needed in React 17+
    }
};

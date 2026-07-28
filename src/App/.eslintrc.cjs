// Renamed from .eslintrc.js: package.json sets "type": "module", so Node treats a
// .js config as ESM and `module.exports` throws. The .cjs extension keeps it CommonJS.
//
// The `react-app` / `react-app/jest` presets were dropped. They come from
// eslint-config-react-app, a Create React App package that is not in
// devDependencies and not installed — this project moved to Vite. Extending a
// missing config made `npm run lint` fail outright.
//
// react-app also supplied the react-hooks rules, so eslint-plugin-react-hooks is
// added explicitly below. Without it the codebase's existing
// `// eslint-disable-line react-hooks/exhaustive-deps` refers to a rule that no
// longer exists, which eslint reports as an error — and, more importantly, the
// hooks rules that catch stale closures and missing dependencies would be lost.
module.exports = {
    root: true,
    extends: [
        'eslint:recommended',
        'plugin:@typescript-eslint/recommended',
        'plugin:react/recommended',
        'plugin:react-hooks/recommended',
    ],
    parser: '@typescript-eslint/parser',
    plugins: ['react', 'react-hooks', '@typescript-eslint'],
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
        // TypeScript already checks prop shapes at compile time; runtime
        // propTypes are redundant in a .tsx codebase.
        'react/prop-types': 'off',
    }
};

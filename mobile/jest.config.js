/**
 * Jest config for pure-logic unit tests (no React Native runtime).
 * Component/e2e tests (Maestro/Detox) are configured separately in M5.
 */
module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'node',
  testMatch: ['**/__tests__/**/*.test.ts'],
  transform: {
    '^.+\\.ts$': [
      'ts-jest',
      {
        tsconfig: {
          jsx: 'react',
          esModuleInterop: true,
          skipLibCheck: true,
          types: ['jest', 'node'],
        },
      },
    ],
  },
};

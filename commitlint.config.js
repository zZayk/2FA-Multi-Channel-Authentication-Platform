// Commit message contract — read by .github/workflows/commitlint.yml.
//
// Format: <type>(<scope>): <subject>
//   feat(otp): generate 6-digit code with secrets module
//   fix(api):  reject expired OTP with 410
//   docs(readme): update arch diagram
//   chore(ci): bump ruff to 0.7
//
// Subject ≤ 72 chars, lowercase, no trailing period.
// Full spec: https://www.conventionalcommits.org/

module.exports = {
  extends: ['@commitlint/config-conventional'],
  rules: {
    'subject-case': [2, 'always', ['lower-case', 'sentence-case']],
    'header-max-length': [2, 'always', 72],
    'body-max-line-length': [1, 'always', 100],
    'type-enum': [
      2,
      'always',
      [
        'feat',
        'fix',
        'docs',
        'style',
        'refactor',
        'perf',
        'test',
        'build',
        'ci',
        'chore',
        'revert',
      ],
    ],
  },
};
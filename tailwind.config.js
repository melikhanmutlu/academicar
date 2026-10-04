// Tailwind build for the server-rendered templates (replaces the Play CDN,
// which compiled CSS in the browser). Theme matches the old inline
// `tailwind.config` in templates/base.html.
// Rebuild after template changes:  npm run build:css
module.exports = {
  content: [
    './templates/**/*.html',
    './static/js/**/*.js',
    './*.py',
    './services/**/*.py',
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'sans-serif'],
        serif: ['Montserrat', 'sans-serif'],
      },
      colors: {
        academic: {
          50: '#f5f5f5',
          100: '#efefef',
          500: '#6b6b6b',
          600: '#4d4d4d',
          700: '#202020',
          800: '#202020',
          900: '#202020',
        },
      },
    },
  },
  plugins: [],
};

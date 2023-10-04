/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: ["./templates/**/*.{html,js}", "./static/**/*.{html,js}", "./static/preline/preline.js"],
  plugins: [
    require('./static/preline/plugin'),
  ],
}